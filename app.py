from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import shutil
import tempfile
import traceback
import warnings
from datetime import datetime, timezone
from itertools import product as iterproduct
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
import torch

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

app = FastAPI(title="Biometric Enrollment API")

#  Base directories 
BACKEND_DIR       = Path(__file__).resolve().parent
MODELS_DIR        = BACKEND_DIR / "models"
WIFAKEY_ROOT      = BACKEND_DIR / "WiFaKey"
LDPC_METADATA_DIR = WIFAKEY_ROOT / "LDPC_MetaData"
WIFAKEY_DIR       = WIFAKEY_ROOT  # alias kept for GM paths

#  Auto-resolve helper  ← MUST be defined before the path assignments below
def _resolve(candidates: list) -> Path:
    for p in candidates:
        if Path(p).exists():
            return Path(p)
    raise FileNotFoundError(
        "None of these paths exist:\n" + "\n".join(f"  {p}" for p in candidates)
    )

ADAFACE_WEIGHTS    = _resolve([
    MODELS_DIR / "adaface_ir_101.onnx",
    MODELS_DIR / "adaface" / "adaface_ir_101.onnx",
])
RETINAFACE_WEIGHTS = _resolve([
    MODELS_DIR / "retinaface.onnx",
    MODELS_DIR / "retinaface_mv2.onnx",
    MODELS_DIR / "retinaface" / "retinaface.onnx",
    MODELS_DIR / "retinaface" / "retinaface_mv2.onnx",
])
BG0_PATH  = str(_resolve([LDPC_METADATA_DIR / "BaseGraph" / "BaseGraph2_Set0.txt"]))
GM16_PATH = str(_resolve([LDPC_METADATA_DIR / "BaseGraph_GM" / "LDPC_GM_BG2_16.txt"]))
GM3_PATH  = str(_resolve([
    LDPC_METADATA_DIR / "BaseGraph_GM" / "LDPC_GM_BG2_3.txt",
    WIFAKEY_DIR       / "BaseGraph_GM" / "LDPC_GM_BG2_3.txt",
]))
GM10_PATH = str(_resolve([LDPC_METADATA_DIR / "BaseGraph_GM" / "LDPC_GM_BG2_10.txt"]))
GM6_PATH  = str(_resolve([LDPC_METADATA_DIR / "BaseGraph_GM" / "LDPC_GM_BG2_6.txt"]))

WEIGHTS_DIR = LDPC_METADATA_DIR / "Weights_Var_MS"
BIASES_DIR  = LDPC_METADATA_DIR / "Biases_Var_MS"

DATA_DIR   = BACKEND_DIR / "data" / "enrollments"
FRAMES_DIR = BACKEND_DIR / "data" / "frames"

# Phase 1 — RetinaFace config
RETINAFACE_CFG = {
    'min_sizes':   [[16, 32], [64, 128], [256, 512]],
    'steps':       [8, 16, 32],
    'variance':    [0.1, 0.2],
    'clip':        False,
    'out_channel': 64,
}

FRAMES_TO_USE  = 20
FACE_SIZE      = 112
RETINA_H       = 640
RETINA_W       = 640
RGB_MEAN       = (104, 117, 123)
CONF_THRESHOLD = 0.6
NMS_THRESHOLD  = 0.4
PRE_NMS_TOPK   = 5000

REFERENCE_PTS = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.6963],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.3655],
], dtype=np.float32)

#  Phase 3 — quantisation config 
QUANT_SCALE  = 5           # single scale used for enrollment
QUANT_BITS   = 3
EMB_DIM      = 512
BITVEC_LEN   = EMB_DIM * QUANT_BITS   # 1536 bits

# Phase 4 — LDPC config
LDPC_N      = 52
LDPC_M      = 42
LDPC_Z      = 16
LDPC_CODE_N = LDPC_N * LDPC_Z          # 832 bits
LDPC_CODE_K = (LDPC_N - LDPC_M) * LDPC_Z  # 160 bits


#  PHASE 1 — RETINAFACE INFRASTRUCTURE

class PriorBox:
    def __init__(self, cfg, image_size):
        self.image_size   = image_size
        self.clip         = cfg['clip']
        self.steps        = cfg['steps']
        self.min_sizes    = cfg['min_sizes']
        self.feature_maps = [
            [math.ceil(image_size[0] / s), math.ceil(image_size[1] / s)]
            for s in self.steps
        ]

    def generate_anchors(self):
        anchors = []
        for k, (map_h, map_w) in enumerate(self.feature_maps):
            step = self.steps[k]
            for i, j in iterproduct(range(map_h), range(map_w)):
                for min_size in self.min_sizes[k]:
                    s_kx = min_size / self.image_size[1]
                    s_ky = min_size / self.image_size[0]
                    cx   = (j + 0.5) * step / self.image_size[1]
                    cy   = (i + 0.5) * step / self.image_size[0]
                    anchors += [cx, cy, s_kx, s_ky]
        out = torch.tensor(anchors, dtype=torch.float32).view(-1, 4)
        if self.clip:
            out.clamp_(0, 1)
        return out


def _decode_boxes(loc, priors, variances):
    cxcy  = priors[:, :2] + loc[:, :2] * variances[0] * priors[:, 2:]
    wh    = priors[:, 2:] * torch.exp(loc[:, 2:] * variances[1])
    boxes = torch.empty_like(loc)
    boxes[:, :2] = cxcy - wh / 2
    boxes[:, 2:] = cxcy + wh / 2
    return boxes


def _decode_landmarks(predictions, priors, variances):
    pred = predictions.view(predictions.size(0), 5, 2)
    lm   = (priors[:, :2].unsqueeze(1)
            + pred * variances[0] * priors[:, 2:].unsqueeze(1))
    return lm.view(lm.size(0), -1)


def _nms(dets, threshold):
    x1, y1, x2, y2, scores = (
        dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    )
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep  = []
    while order.size > 0:
        i = order[0]; keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w   = np.maximum(0.0, xx2 - xx1 + 1)
        h   = np.maximum(0.0, yy2 - yy1 + 1)
        ovr = (w * h) / (areas[i] + areas[order[1:]] - w * h)
        order = order[np.where(ovr <= threshold)[0] + 1]
    return keep


class RetinaFaceDetector:
    def __init__(self, model_path: str):
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if "CUDAExecutionProvider" in ort.get_available_providers()
            else ["CPUExecutionProvider"]
        )
        self.session = ort.InferenceSession(model_path, providers=providers)
        self.cfg     = RETINAFACE_CFG
        logger.info(f"RetinaFace ONNX loaded | {providers[0]}")

    def _preprocess(self, frame):
        img  = np.float32(frame)
        img -= np.array(RGB_MEAN, dtype=np.float32)
        img  = img.transpose(2, 0, 1)
        return np.expand_dims(img, 0)

    def detect(self, frame):
        img_h, img_w = frame.shape[:2]
        inp          = self._preprocess(frame)
        outputs      = self.session.run(None, {'input': inp})
        loc_raw      = torch.tensor(outputs[0].squeeze(0))
        conf_raw     = outputs[1].squeeze(0)
        lm_raw       = torch.tensor(outputs[2].squeeze(0))

        priorbox  = PriorBox(self.cfg, image_size=(img_h, img_w))
        priors    = priorbox.generate_anchors()
        boxes     = _decode_boxes(loc_raw, priors, self.cfg['variance'])
        landmarks = _decode_landmarks(lm_raw, priors, self.cfg['variance'])

        bbox_scale = torch.tensor([img_w, img_h, img_w, img_h], dtype=torch.float32)
        boxes      = (boxes * bbox_scale).cpu().numpy()
        lm_scale   = torch.tensor([img_w, img_h] * 5, dtype=torch.float32)
        landmarks  = (landmarks * lm_scale).cpu().numpy()
        scores     = conf_raw[:, 1]

        mask = scores > CONF_THRESHOLD
        boxes, landmarks, scores = boxes[mask], landmarks[mask], scores[mask]
        if len(scores) == 0:
            return None

        order     = scores.argsort()[::-1][:PRE_NMS_TOPK]
        boxes, landmarks, scores = boxes[order], landmarks[order], scores[order]
        dets      = np.hstack((boxes, scores[:, np.newaxis])).astype(np.float32)
        keep      = _nms(dets, NMS_THRESHOLD)
        return dets[keep][0, :4], landmarks[keep][0]


def _umeyama(src, dst):
    n    = src.shape[0]
    mu_s = src.mean(0); mu_d = dst.mean(0)
    sc   = src - mu_s;  dc   = dst - mu_d
    vs   = (sc ** 2).sum() / n
    if vs < 1e-10:
        return None
    cov = (dc.T @ sc) / n
    try:
        U, S, Vt = np.linalg.svd(cov)
    except np.linalg.LinAlgError:
        return None
    d = np.ones(2)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        d[-1] = -1
    R = U @ np.diag(d) @ Vt
    c = (S * d).sum() / vs
    t = mu_d - c * R @ mu_s
    M = np.zeros((2, 3), dtype=np.float32)
    M[:, :2] = c * R
    M[:, 2]  = t
    return M


def _umeyama_align(frame, landmarks):
    src_pts = landmarks.reshape(5, 2).astype(np.float32)
    M       = _umeyama(src_pts, REFERENCE_PTS)
    if M is None:
        return None
    return cv2.warpAffine(
        frame, M, (FACE_SIZE, FACE_SIZE),
        flags=cv2.INTER_LANCZOS4,
        borderMode=cv2.BORDER_REFLECT,
    )

#  PHASE 2 — ADAFACE

class AdaFaceModel:
    def __init__(self, model_path: str):
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if "CUDAExecutionProvider" in ort.get_available_providers()
            else ["CPUExecutionProvider"]
        )
        self.session     = ort.InferenceSession(model_path, providers=providers)
        self.input_name  = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        logger.info(f"AdaFace IR-101 ONNX loaded | {providers[0]}")

    def raw_embedding(self, face_bgr: np.ndarray) -> np.ndarray:
        img = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2RGB)
        img = (img.astype(np.float32) / 255.0 - 0.5) / 0.5
        img = img.transpose(2, 0, 1)[np.newaxis]
        out = self.session.run([self.output_name], {self.input_name: img})
        emb = out[0][0] if out[0].ndim == 2 else out[0]
        return emb.astype(np.float32)


_detector:  Optional[RetinaFaceDetector] = None
_adaface:   Optional[AdaFaceModel]       = None


def _get_models() -> tuple[RetinaFaceDetector, AdaFaceModel]:
    global _detector, _adaface
    if _detector is None:
        _detector = RetinaFaceDetector(str(RETINAFACE_WEIGHTS))
    if _adaface is None:
        _adaface = AdaFaceModel(str(ADAFACE_WEIGHTS))
    return _detector, _adaface

#  PHASE 3 — SCALE × N → CLIP [-1,+1] → 3-BIT GRAY-CODE QUANTISATION

def _to_gray(n: int) -> int:
    return n ^ (n >> 1)


def embedding_to_bitvec(embedding: np.ndarray,
                         scale: int  = QUANT_SCALE,
                         bits:  int  = QUANT_BITS) -> np.ndarray:
    """L2-unit → ×scale → clip[-1,+1] → 3-bit Gray-code → 1536-bit vector."""
    levels = (1 << bits) - 1
    scaled = np.clip(embedding * scale, -1.0, 1.0)
    q      = np.clip(
        np.round((scaled + 1.0) / 2.0 * levels).astype(np.int32),
        0, levels,
    )
    g      = np.array([_to_gray(int(v)) for v in q], dtype=np.int32)
    result = np.zeros(len(g) * bits, dtype=np.uint8)
    for i, val in enumerate(g):
        for b in range(bits):
            result[i * bits + (bits - 1 - b)] = (int(val) >> b) & 1
    return result.astype(np.uint8)

#  PHASE 4 — PROTO_LDPC  (encode only — no decode needed at sign-up)

class Proto_LDPC:
    def __init__(self, N: int, m: int, Z: int):
        self.code_n = N
        self.Z      = Z
        self.code_k = N - m

        gm_map = {
            16: np.loadtxt(GM16_PATH, dtype=int, delimiter=','),
            3:  np.loadtxt(GM3_PATH,  dtype=int, delimiter=','),
            10: np.loadtxt(GM10_PATH, dtype=int, delimiter=','),
            6:  np.loadtxt(GM6_PATH,  dtype=int, delimiter=','),
        }
        if Z not in gm_map:
            raise ValueError(f"Unsupported Z={Z}")
        self.G_matrix = gm_map[Z]
        logger.info(f"Proto_LDPC: code_n={N*Z} bits, code_k={self.code_k*Z} bits")

    def encode_LDPC(self, x_bits: np.ndarray) -> np.ndarray:
        return (np.dot(x_bits, self.G_matrix) % 2).astype(np.uint8)


# Lazy LDPC singleton
_proto_ldpc: Optional[Proto_LDPC] = None

def _get_ldpc() -> Proto_LDPC:
    global _proto_ldpc
    if _proto_ldpc is None:
        _proto_ldpc = Proto_LDPC(N=LDPC_N, m=LDPC_M, Z=LDPC_Z)
    return _proto_ldpc

#  FUZZY COMMITMENT — REGISTRATION ONLY  (sign-up, no recovery)

def _register(bitvec: np.ndarray, proto_ldpc: Proto_LDPC) -> dict:
    """
    Registration (sign-up) step.

    nonce = all-ones (look4noncerate DISABLED).
    Returns helper data and the SHA-256 key hash — both stored on the server.
    Recovery / login decoding is NOT performed here.
    """
    nonce        = np.ones(BITVEC_LEN, dtype=np.uint8)
    bitvec_nonce = np.bitwise_and(bitvec, nonce)
    message_bits = bitvec_nonce[:LDPC_CODE_K].copy()
    codeword     = proto_ldpc.encode_LDPC(message_bits)
    helper       = np.bitwise_xor(
        bitvec_nonce[:LDPC_CODE_N].astype(np.uint8),
        codeword.astype(np.uint8),
    )
    key_hash = hashlib.sha256(np.packbits(message_bits).tobytes()).hexdigest()
    return {
        "nonce":        nonce,
        "bitvec_nonce": bitvec_nonce,
        "message":      message_bits,
        "codeword":     codeword,
        "helper":       helper,
        "key_hash":     key_hash,
    }

#  FULL INLINE ENROLLMENT PIPELINE
#  Runs on the cropped frames that were already written to disk.

def _run_enrollment_pipeline(cropped_dir: Path) -> dict:
    """
    Given the directory of cropped JPEG frames:

      1. Resize each frame to 640×640
      2. RetinaFace detection
      3. Umeyama alignment → 112×112 face chip
      4. AdaFace IR-101 embedding (L2-normalised, averaged over top-N frames)
      5. ×scale → clip[-1,+1] → 3-bit Gray-code quantisation → 1536-bit vector
      6. LDPC fuzzy commitment (registration only — no recovery)
      7. SHA-256 key hash

    Returns a dict with all artefacts needed to build the enrollment JSON record.
    Raises RuntimeError if not enough aligned faces can be extracted.
    """
    detector, adaface = _get_models()
    proto_ldpc        = _get_ldpc()

    # Collect cropped frame paths (sorted for determinism)
    frame_paths = sorted(cropped_dir.glob("*.jpg"))
    if not frame_paths:
        raise RuntimeError(f"No cropped frames found in {cropped_dir}")

    n_no_face = 0
    n_umeyama_fail = 0
    aligned_pool: list[tuple[float, np.ndarray]] = []

    for fp in frame_paths:
        orig_frame = cv2.imread(str(fp))
        if orig_frame is None:
            logger.warning(f"Cannot read cropped frame: {fp}")
            continue

        # Step 1: Resize to 640×640 for RetinaFace 
        frame_retina = cv2.resize(orig_frame, (RETINA_W, RETINA_H),
                                  interpolation=cv2.INTER_LINEAR)

        #  Step 2: Face detection
        result = detector.detect(frame_retina)
        if result is None:
            n_no_face += 1
            continue

        _, lm = result

        # Step 3: Umeyama alignment → 112×112 face chip 
        aligned = _umeyama_align(frame_retina, lm)
        if aligned is None:
            n_umeyama_fail += 1
            continue

        # Sharpness score for top-N selection
        gray      = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        aligned_pool.append((sharpness, aligned))

    logger.info(
        "Alignment pass: no_face=%d  umeyama_fail=%d  aligned=%d",
        n_no_face, n_umeyama_fail, len(aligned_pool),
    )

    if not aligned_pool:
        raise RuntimeError(
            f"Zero aligned faces extracted from {len(frame_paths)} cropped frames. "
            "Ensure the video contains a clearly visible face."
        )

    # Keep top-N sharpest faces
    aligned_pool.sort(key=lambda t: t[0], reverse=True)
    top_frames = aligned_pool[:FRAMES_TO_USE]
    logger.info("Using top %d sharpest aligned faces for embedding", len(top_frames))

    #  Step 4: AdaFace embeddings → L2-normalised average 
    unit_vecs: list[np.ndarray] = []
    for _, face_112 in top_frames:
        raw = adaface.raw_embedding(face_112)
        n   = np.linalg.norm(raw)
        if n > 1e-10:
            unit_vecs.append((raw / n).astype(np.float32))

    if not unit_vecs:
        raise RuntimeError("All embeddings had near-zero norm — cannot enroll.")

    avg      = np.mean(np.stack(unit_vecs, 0), 0).astype(np.float32)
    avg_norm = np.linalg.norm(avg)
    if avg_norm < 1e-10:
        raise RuntimeError("Average embedding is near-zero — cannot enroll.")

    embedding = (avg / avg_norm).astype(np.float32)
    logger.info(
        "Final embedding: dim=%d  norm=%.6f  mean=%.4f  std=%.4f",
        embedding.shape[0], np.linalg.norm(embedding),
        embedding.mean(), embedding.std(),
    )

    # Step 5: Quantisation → 1536-bit vector 
    bitvec = embedding_to_bitvec(embedding, scale=QUANT_SCALE, bits=QUANT_BITS)
    logger.info("Bitvec: len=%d  sum=%d  density=%.4f",
                len(bitvec), int(bitvec.sum()), bitvec.mean())

    # Step 6 + 7: LDPC fuzzy commitment + SHA-256 key hash 
    reg = _register(bitvec, proto_ldpc)
    logger.info("Registration complete. key_hash=%s", reg["key_hash"])

    # Pack binary arrays to base64 for compact JSON storage
    def _pack_b64(arr: np.ndarray) -> str:
        return base64.b64encode(np.packbits(arr).tobytes()).decode()

    return {
        # Core cryptographic outputs
        "key_hash":          reg["key_hash"],
        "embedding":         embedding,

        # Helper / nonce / bitvec (packed as base64)
        "helper_bits_b64":   _pack_b64(reg["helper"]),
        "helper_bit_length": int(len(reg["helper"])),
        "nonce_bits_b64":    _pack_b64(reg["nonce"]),
        "nonce_bit_length":  int(len(reg["nonce"])),
        "bitvec_bits_b64":   _pack_b64(bitvec),
        "bitvec_length":     int(len(bitvec)),
        "quant_scale":       QUANT_SCALE,

        # Diagnostics
        "metadata": {
            "embedding_norm": float(np.linalg.norm(embedding)),
            "embedding_mean": float(embedding.mean()),
            "embedding_std":  float(embedding.std()),
            "quant_scale":    QUANT_SCALE,
            "quant_bits":     QUANT_BITS,
            "bitvec_len":     int(len(bitvec)),
            "ldpc_code_n":    LDPC_CODE_N,
            "ldpc_code_k":    LDPC_CODE_K,
            "n_aligned_faces": len(aligned_pool),
            "n_used_faces":    len(top_frames),
        },
    }

#  FRAME UTILITIES  (unchanged from original)

def extract_and_crop_frames(video_path: Path, output_dir: Path, username: str) -> dict:
    """
    Extract frames from video, save originals and cropped versions.
    Returns dict with counts and paths.

    Crop ratio: 1.08 from center means we keep 1/1.08 ≈ 0.93 of each dimension
    (a square centre-crop, ~10% trimmed per side).
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        logger.warning(f"Cannot open video for frame extraction: {video_path}")
        return {"total_frames": 0, "original_dir": "", "cropped_dir": ""}

    user_frames_dir = output_dir / username
    frames_dir      = user_frames_dir / "original_frames"
    cropped_dir     = user_frames_dir / "cropped_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    cropped_dir.mkdir(parents=True, exist_ok=True)

    frame_count = 0
    crop_ratio  = 1.08

    logger.info(f"Extracting frames from video: {video_path}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        h, w   = frame.shape[:2]
        side    = min(h, w)
        new_side = int(side / crop_ratio)

        # Save original
        cv2.imwrite(str(frames_dir / f"frame_{frame_count:04d}.jpg"), frame)

        # Centre-square crop
        start_y = max(0, (h - new_side) // 2)
        end_y   = min(h, start_y + new_side)
        start_x = max(0, (w - new_side) // 2)
        end_x   = min(w, start_x + new_side)
        cropped = frame[start_y:end_y, start_x:end_x]
        cv2.imwrite(str(cropped_dir / f"cropped_{frame_count:04d}.jpg"), cropped)

        frame_count += 1
        if frame_count >= 60:
            logger.info("Reached maximum frame limit (60), stopping extraction")
            break

    cap.release()
    logger.info("Extracted %d frames for user %s", frame_count, username)

    return {
        "total_frames": frame_count,
        "original_dir": str(frames_dir),
        "cropped_dir":  str(cropped_dir),
    }


def _extract_best_frame(video_path: Path) -> np.ndarray | None:
    """Return the single sharpest frame (highest Laplacian variance)."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None

    best_frame: np.ndarray | None = None
    best_score = -1.0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        score = cv2.Laplacian(gray, cv2.CV_64F).var()
        if score > best_score:
            best_score = score
            best_frame = frame.copy()

    cap.release()
    return best_frame

#  API ROUTES

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/enroll")
async def enroll(
    username:     str = Form(...),
    video:        UploadFile = File(...),
    frame_width:  int = Form(0),
    frame_height: int = Form(0),
) -> dict[str, object]:
    """
    Sign-up endpoint.

    Accepts:
      - username      : plain-text username
      - video         : raw video file from the Flutter app
      - frame_width   : original camera sensor frame width  (pixels)
      - frame_height  : original camera sensor frame height (pixels)

    Server saves to disk
    ────────────────────
      data/enrollments/<username>.json
      data/frames/<username>/original_frames/
      data/frames/<username>/cropped_frames/
      data/frames/<username>_best.jpg

    Flutter app receives ONLY
    ─────────────────────────
      { "hashkey": "<hex>" }
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    #  Reject duplicate username 
    record_path = DATA_DIR / f"{username}.json"
    if record_path.exists():
        raise HTTPException(
            status_code=409,
            detail="You already have an account. Please log in.",
        )

    #  Pre-load existing embeddings for duplicate-face check 
    _existing_embeddings: list[tuple[str, np.ndarray]] = []
    for existing_json in DATA_DIR.glob("*.json"):
        try:
            existing_record = json.loads(existing_json.read_text(encoding="utf-8"))
            emb = existing_record.get("enrollments", [{}])[0].get("embedding")
            if emb:
                _existing_embeddings.append(
                    (existing_record.get("username", existing_json.stem),
                     np.array(emb, dtype=np.float32))
                )
        except Exception:
            continue

    #  Save incoming video to a temp file 
    suffix = Path(video.filename or "enrollment.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(video.file, tmp)
        temp_path = Path(tmp.name)

    try:
        logger.info("Starting enrollment for username=%s", username)

        #  Step A: Extract and crop frames 
        frame_extraction_result = extract_and_crop_frames(temp_path, FRAMES_DIR, username)
        cropped_dir = Path(frame_extraction_result["cropped_dir"])

        if frame_extraction_result["total_frames"] == 0:
            raise HTTPException(
                status_code=422,
                detail="Could not read any frames from the uploaded video.",
            )

        # Step B: Run inline pipeline on cropped frames 
        #    Resize 640×640 → RetinaFace → Umeyama → AdaFace →
        #    Quantise → LDPC commitment → SHA-256 key hash
        artifacts = _run_enrollment_pipeline(cropped_dir)

        #  Step C: Duplicate-face check 
        FACE_SIMILARITY_THRESHOLD = 0.80
        new_emb      = artifacts["embedding"].astype(np.float32)
        new_emb_norm = new_emb / (np.linalg.norm(new_emb) + 1e-10)

        for stored_username, stored_emb in _existing_embeddings:
            stored_norm = stored_emb / (np.linalg.norm(stored_emb) + 1e-10)
            similarity  = float(np.dot(new_emb_norm, stored_norm))
            logger.info(
                "Face similarity: new=%s vs stored=%s  sim=%.4f",
                username, stored_username, similarity,
            )
            if similarity >= FACE_SIMILARITY_THRESHOLD:
                raise HTTPException(
                    status_code=409,
                    detail="You already have an account. Please log in.",
                )

        #  Step D: Best (sharpest) frame for preview 
        best_frame = _extract_best_frame(temp_path)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        best_frame_filename = f"{username}_{ts}_best.jpg"
        best_frame_path     = FRAMES_DIR / best_frame_filename

        if best_frame is not None:
            cv2.imwrite(str(best_frame_path), best_frame)
            logger.info("Best frame saved → %s", best_frame_path)
        else:
            logger.warning("Could not extract any frame for username=%s", username)
            best_frame_filename = None

        #  Step E: Build and persist the enrollment record 
        original_frame_size = (
            f"{min(frame_width, frame_height)} X {max(frame_width, frame_height)}"
            if frame_width > 0 and frame_height > 0
            else "unknown"
        )

        helper_data = {
            "username":          username,
            "originalFrameSize": original_frame_size,
            "embeddingModel":    "AdaFace IR-101",
            "detectorModel":     "RetinaFace",
            "pipeline":          (
                "RetinaFace + AdaFace IR-101 + "
                "Gray-code quantisation + LDPC fuzzy commitment"
            ),
            "embeddingNorm":  artifacts["metadata"]["embedding_norm"],
            "embeddingMean":  artifacts["metadata"]["embedding_mean"],
            "embeddingStd":   artifacts["metadata"]["embedding_std"],
            "quantScale":     artifacts["metadata"]["quant_scale"],
            "quantBits":      artifacts["metadata"]["quant_bits"],
            "bitvecLen":      artifacts["metadata"]["bitvec_len"],
            "ldpcCodeN":      artifacts["metadata"]["ldpc_code_n"],
            "ldpcCodeK":      artifacts["metadata"]["ldpc_code_k"],
        }

        new_entry = {
            "enrolled_at":  datetime.now(timezone.utc).isoformat(),
            "hashkey":      artifacts["key_hash"],
            "helper_data":  helper_data,
            "embedding":    artifacts["embedding"].tolist(),
            "best_frame_file": best_frame_filename,
            "frame_extraction": {
                "total_frames":       frame_extraction_result["total_frames"],
                "original_frames_dir": frame_extraction_result["original_dir"],
                "cropped_frames_dir":  frame_extraction_result["cropped_dir"],
                "crop_ratio":          1.08,
                "crop_description":    "~10% trimmed per side (square centre-crop)",
            },
            "helperBits": {
                "encoding":  "base64-packed-bits",
                "bitLength": artifacts["helper_bit_length"],
                "data":      artifacts["helper_bits_b64"],
            },
            "nonce": {
                "encoding":  "base64-packed-bits",
                "bitLength": artifacts["nonce_bit_length"],
                "data":      artifacts["nonce_bits_b64"],
            },
            "bitVector": {
                "encoding":  "base64-packed-bits",
                "bitLength": artifacts["bitvec_length"],
                "data":      artifacts["bitvec_bits_b64"],
            },
            "quantScale": artifacts["quant_scale"],
            "metadata":   artifacts["metadata"],
        }

        record = {"username": username, "enrollments": [new_entry]}
        record_path.write_text(
            json.dumps(record, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "Enrollment saved  username=%s  hashkey=%s  frames=%d",
            username, artifacts["key_hash"],
            frame_extraction_result["total_frames"],
        )

        # Return ONLY the hashkey to the Flutter app 
        return {"hashkey": artifacts["key_hash"]}

    except HTTPException:
        raise

    except Exception as exc:
        logger.error(
            "Enrollment failed for username=%s\n%s",
            username, traceback.format_exc(),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    finally:
        temp_path.unlink(missing_ok=True)

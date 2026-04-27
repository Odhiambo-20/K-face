import 'dart:async';
import 'dart:io';
import 'package:flutter/services.dart';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
//import 'package:google_mlkit_face_detection/google_mlkit_face_detection.dart';
import 'package:permission_handler/permission_handler.dart';

import '../models/enrollment_draft.dart';
import '../services/biometric_enrollment_api.dart';
import '../theme/app_theme.dart';
import '../widgets/app_background.dart';
import '../widgets/primary_button.dart';
import '../widgets/tick_ring.dart';
import 'enrollment_key_result_screen.dart';

class LookAtCameraScreen extends StatefulWidget {
  const LookAtCameraScreen({
    super.key,
    required this.draft,
  });

  static const route = '/look';
  final EnrollmentDraft draft;

  @override
  State<LookAtCameraScreen> createState() => _LookAtCameraScreenState();
}

class _LookAtCameraScreenState extends State<LookAtCameraScreen>
    with WidgetsBindingObserver {
  static const _analysisThrottle = Duration(milliseconds: 200);

  // Face-detection thresholds (3:4 only)
  static const double _minNormW = 0.15;
  static const double _maxNormW = 0.40;
  static const double _minNormH = 0.22;
  static const double _maxNormH = 0.50;
  static const double _minArea  = 0.05;
  static const double _maxArea  = 0.22;

  // Consecutive good frames required before triggering capture
  static const int _requiredGoodFrames = 3;
  int _consecutiveGoodFrames = 0;

  // Video recording constants
  static const int _countdownSeconds = 3;
  static const int _recordDurationSeconds = 5;
  static const double _bottomPanelHeight = 250;

  // Camera / detector state
  CameraController? _controller;
  Future<void>? _initializeFuture;

  /*

  final FaceDetector _faceDetector = FaceDetector(
    options: FaceDetectorOptions(
      performanceMode: FaceDetectorMode.fast,
      enableContours: false,
      enableLandmarks: false,
    ),
  );

  */

  final BiometricEnrollmentApi _api = BiometricEnrollmentApi();

  bool _isAnalyzing = false;
  bool _isStreaming = false;
  bool _isBusy = false;
  bool _isUploading = false;
  bool _didTriggerCapture = false;
  bool _isRecording = false;
  DateTime? _lastAnalysisAt;
  double _lastFrameW = 0;
  double _lastFrameH = 0;
  double _uploadProgress = 0.0;
  int _countdownValue = _countdownSeconds;
  Timer? _countdownTimer;
  Timer? _recordTimer;
  String _status = 'Center your face inside the ring.';
  String? _cameraError;
  String? _uploadErrorDetail;

  @override
  void initState() {
    super.initState();
    SystemChrome.setPreferredOrientations([
    DeviceOrientation.portraitUp,
  ]);
    WidgetsBinding.instance.addObserver(this);
    _api.loadEndpoint().then((_) {
      if (mounted) setState(() {});
    });
    _initializeCamera();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    SystemChrome.setPreferredOrientations(
    DeviceOrientation.values,
  );
    //_faceDetector.close();
    _controller?.dispose();
    _controller = null;
    _initializeFuture = null;
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final controller = _controller;
    if (controller == null || !controller.value.isInitialized) return;

    if (state == AppLifecycleState.inactive ||
        state == AppLifecycleState.paused) {
      controller.dispose();
      _controller = null;
      _initializeFuture = null;
      _isStreaming = false;
      _isBusy = false;
      if (mounted) setState(() {});
      return;
    }
    if (state == AppLifecycleState.resumed) {
      _initializeCamera();
    }
  }

  // ─── Camera init ─────

  Future<void> _initializeCamera() async {
    final previousController = _controller;
    _controller = null;
    _initializeFuture = null;
    await previousController?.dispose();

    setState(() {
      _cameraError = null;
      _uploadErrorDetail = null;
      _status = 'Center your face inside the ring.';
    });

    final permission = await Permission.camera.request();
    if (!permission.isGranted) {
      setState(() {
        _cameraError = 'Camera permission is required.';
        _status = 'Camera permission is required.';
      });
      return;
    }

    try {
      final cameras = await availableCameras();
      if (cameras.isEmpty) {
        setState(() {
          _cameraError = 'No camera was found on this device.';
          _status = 'Unable to initialize the camera.';
        });
        return;
      }

      final frontCameras = cameras
          .where((c) => c.lensDirection == CameraLensDirection.front)
          .toList();

      final front = frontCameras.isNotEmpty ? frontCameras.first : cameras.first;

      final controller = await _buildFrontCameraController(front);
      _controller = controller;
      _initializeFuture = Future<void>.value();

      if (!mounted) {
        await controller.dispose();
        return;
      }
      setState(() {});

      await _safeLockPortrait(controller);
      await _safeSetExposureLocked(controller);
      await _safeSetMinZoom(controller);

      if (controller.value.isInitialized) {
        final size = controller.value.previewSize;
        if (size != null) {
          debugPrint('Camera: ${size.width}x${size.height}  ratio: ${size.width / size.height}');
        }
      }

      if (!mounted) return;
      await _startFaceTracking();
      await _autoStartRecording();
      setState(() {});
    } on CameraException catch (e) {
      setState(() {
        _cameraError = _formatCameraError(e);
        _status = 'Unable to initialize the camera.';
      });
    } catch (e) {
      setState(() {
        _cameraError = e.toString();
        _status = 'Unable to initialize the camera.';
      });
    }
  }

  Future<CameraController> _buildFrontCameraController(
    CameraDescription front,
  ) async {
    final presetsToTry = <ResolutionPreset>[
      ResolutionPreset.medium,
      ResolutionPreset.high,
      ResolutionPreset.veryHigh,
      ResolutionPreset.low,
    ];

    CameraException? lastCameraException;
    Object? lastError;

    for (final preset in presetsToTry) {
      final controller = CameraController(
        front,
        preset,
        enableAudio: false,
        imageFormatGroup: Platform.isIOS
            ? ImageFormatGroup.bgra8888
            : ImageFormatGroup.yuv420,
      );

      try {
        await controller.initialize();
        return controller;
      } on CameraException catch (e) {
        lastCameraException = e;
        await controller.dispose();
      } catch (e) {
        lastError = e;
        await controller.dispose();
      }
    }

    if (lastCameraException != null) {
      throw lastCameraException!;
    }
    throw lastError ?? Exception('Unable to initialize the front camera.');
  }

  Future<void> _safeLockPortrait(CameraController controller) async {
    try {
      await controller.lockCaptureOrientation(DeviceOrientation.portraitUp);
    } catch (e, st) {
      debugPrint('[Camera] lockCaptureOrientation failed: $e\n$st');
    }
  }

  Future<void> _safeSetExposureLocked(CameraController controller) async {
    try {
      await controller.setExposureMode(ExposureMode.auto);
    } catch (e, st) {
      debugPrint('[Camera] setExposureMode failed: $e\n$st');
    }
  }

  Future<void> _safeSetMinZoom(CameraController controller) async {
    try {
      final minZoom = await controller.getMinZoomLevel();
      await controller.setZoomLevel(minZoom);
    } catch (e, st) {
      debugPrint('[Camera] setZoomLevel failed: $e\n$st');
    }
  }

  String _formatCameraError(CameraException e) {
    final description = e.description?.trim();
    if (description != null && description.isNotEmpty) {
      return description;
    }
    return e.code;
  }

  // ─── Face tracking ──────

  Future<void> _startFaceTracking() async {
    final controller = _controller;
    if (controller == null ||
        !controller.value.isInitialized ||
        controller.value.isStreamingImages ||
        _isBusy) {
      return;
    }

    _isStreaming = true;
    _didTriggerCapture = false;
    //_consecutiveGoodFrames = 0;
    _setStatus('Center your face inside the ring.');

    await controller.startImageStream((image) async {
      if (_isAnalyzing || _isBusy || _isUploading || _didTriggerCapture) return;

      final now = DateTime.now();
      final last = _lastAnalysisAt;
      if (last != null && now.difference(last) < _analysisThrottle) return;
      _lastAnalysisAt = now;

      _isAnalyzing = true;
      try {
        //final inputImage = _buildInputImage(controller, image);
        //if (inputImage == null) return;

        /*

        //final faces = await _faceDetector.processImage(inputImage);
        _handleFaces(faces, image.width.toDouble(), image.height.toDouble());
        
        */

      } catch (e, st) {
        debugPrint('[FaceDetect] $e\n$st');
      } finally {
        _isAnalyzing = false;
      }
    });
  }

  // Add this method after _startFaceTracking()
    Future<void> _autoStartRecording() async {
      // Wait a moment for camera to stabilize
      await Future.delayed(const Duration(milliseconds: 1500));
      
      if (mounted && !_isBusy && !_isUploading && !_didTriggerCapture) {
        debugPrint('[Auto] Starting recording sequence');
        _didTriggerCapture = true;
        await _startVideoRecordingSequence();
      }
    }


  /*

  InputImage? _buildInputImage(CameraController controller, CameraImage image) {
    final rotation = _inputRotationFromSensor(
      controller.description.sensorOrientation,
      controller.description.lensDirection,
    );

    if (Platform.isIOS) {
      final format = InputImageFormatValue.fromRawValue(image.format.raw);
      if (format == null) {
        debugPrint('[FaceDetect] iOS: unknown format raw=${image.format.raw}');
        return null;
      }
      final plane = image.planes.first;
      return InputImage.fromBytes(
        bytes: plane.bytes,
        metadata: InputImageMetadata(
          size: Size(image.width.toDouble(), image.height.toDouble()),
          rotation: rotation,
          format: format,
          bytesPerRow: plane.bytesPerRow,
        ),
      );
    }

    

    if (image.planes.length < 3) {
      debugPrint('[FaceDetect] Expected 3 planes, got ${image.planes.length}');
      return null;
    }

    final int width  = image.width;
    final int height = image.height;

    final Uint8List yPlane      = image.planes[0].bytes;
    final Uint8List uPlane      = image.planes[1].bytes;
    final Uint8List vPlane      = image.planes[2].bytes;
    final int      yRowStride   = image.planes[0].bytesPerRow;
    final int      uvRowStride  = image.planes[1].bytesPerRow;
    final int      uvPixelStride = image.planes[1].bytesPerPixel ?? 1;

    final nv21 = Uint8List(width * height + (width * height ~/ 2));
    int idx = 0;

    for (int row = 0; row < height; row++) {
      nv21.setRange(idx, idx + width, yPlane, row * yRowStride);
      idx += width;
    }
    for (int row = 0; row < height ~/ 2; row++) {
      for (int col = 0; col < width ~/ 2; col++) {
        final int uvOffset = row * uvRowStride + col * uvPixelStride;
        nv21[idx++] = vPlane[uvOffset];
        nv21[idx++] = uPlane[uvOffset];
      }
    }

    return InputImage.fromBytes(
      bytes: nv21,
      metadata: InputImageMetadata(
        size: Size(width.toDouble(), height.toDouble()),
        rotation: rotation,
        format: InputImageFormat.nv21,
        bytesPerRow: width,
      ),
    );
  }

  /// Correct rotation for both front and rear cameras on Android and iOS.
  InputImageRotation _inputRotationFromSensor(
      int sensorOrientation, CameraLensDirection lensDirection) {
    final isFront = lensDirection == CameraLensDirection.front;

    if (Platform.isAndroid && isFront) {
      // Front camera on Android: invert rotation to compensate for mirroring
      switch (sensorOrientation) {
        case 90:  return InputImageRotation.rotation270deg;
        case 270: return InputImageRotation.rotation90deg;
        case 180: return InputImageRotation.rotation180deg;
        default:  return InputImageRotation.rotation0deg;
      }
    }

    // Rear camera (Android) or any iOS camera
    switch (sensorOrientation) {
      case 90:  return InputImageRotation.rotation90deg;
      case 180: return InputImageRotation.rotation180deg;
      case 270: return InputImageRotation.rotation270deg;
      default:  return InputImageRotation.rotation0deg;
    }
  }

  
  void _handleFaces(List<Face> faces, double imageWidth, double imageHeight) {
    if (faces.isEmpty) {
      _consecutiveGoodFrames = 0;
      return;
    }

    final face = faces.reduce((best, c) =>
        c.boundingBox.width * c.boundingBox.height >
                best.boundingBox.width * best.boundingBox.height
            ? c
            : best);

    _lastFrameW = imageWidth;
    _lastFrameH = imageHeight;

    if (_isFaceInsideGuide(face.boundingBox, imageWidth, imageHeight)) {
      _consecutiveGoodFrames++;
      if (_consecutiveGoodFrames >= _requiredGoodFrames) {
        _didTriggerCapture = true;
        unawaited(_captureAndUpload());
      }
    } else {
      _consecutiveGoodFrames = 0;
    }
  }


  ({double left, double top, double right, double bottom}) _toDisplayRect(
      Rect box, double imageWidth, double imageHeight) {
    if (imageWidth > imageHeight) {
      return (
        left:   1.0 - (box.bottom / imageHeight),
        top:    box.left  / imageWidth,
        right:  1.0 - (box.top    / imageHeight),
        bottom: box.right / imageWidth,
      );
    } else {
      return (
        left:   1.0 - (box.right  / imageWidth),
        top:    box.top    / imageHeight,
        right:  1.0 - (box.left   / imageWidth),
        bottom: box.bottom / imageHeight,
      );
    }
  }

  bool _isFaceInsideGuide(Rect box, double imageWidth, double imageHeight) {
    final r = _toDisplayRect(box, imageWidth, imageHeight);

    final normW   = r.right  - r.left;
    final normH   = r.bottom - r.top;
    final centerX = r.left + normW / 2;
    final centerY = r.top  + normH / 2;
    final area    = normW * normH;

    const targetCenterY    = 0.38;
    const margin           = 0.03;

    final isFullyContained = r.left  >= margin &&
                             r.right  <= (1.0 - margin) &&
                             r.top    >= margin &&
                             r.bottom <= (1.0 - margin);

    final isWellCentered      = (centerX - 0.5).abs()        <= 0.12 &&
                                (centerY - targetCenterY).abs() <= 0.10;
    final isWidthAppropriate  = normW >= _minNormW && normW <= _maxNormW;
    final isHeightAppropriate = normH >= _minNormH && normH <= _maxNormH;
    final isAreaAppropriate   = area  >= _minArea  && area  <= _maxArea;

    return isFullyContained &&
           isWellCentered &&
           isWidthAppropriate &&
           isHeightAppropriate &&
           isAreaAppropriate;
  }

  */

  void _setStatus(String next) {
    if (!mounted || _status == next) return;
    setState(() => _status = next);
  }

  // ─── Video recording sequence (3s invisible countdown + 1s record with green ring) ───

  Future<void> _startVideoRecordingSequence() async {
    debugPrint('[Sequence] Entered. isBusy=$_isBusy isUploading=$_isUploading');  // ADD
    final controller = _controller;
    if (controller == null || _isBusy || _isUploading) return;
    if (mounted) setState(() => _uploadErrorDetail = null);

    try {
      // Stop the image stream before recording
      if (controller.value.isStreamingImages) {
        await controller.stopImageStream();
        debugPrint('[Sequence] Image stream stopped');  // ADD
      }
      _isStreaming = false;
      _isBusy = true;
      debugPrint('[Sequence] Countdown starting...');  // ADD
      
      // Reset countdown (invisible to user)
      _countdownValue = _countdownSeconds;
      
      // Start invisible countdown
      _countdownTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
        debugPrint('[Sequence] Countdown tick: $_countdownValue');  // ADD
        if (!mounted) {
          timer.cancel();
          return;
        }
        
        _countdownValue--;
        
        if (_countdownValue == 0) {
          timer.cancel();
          _startVideoRecording();
        }
      });
    } catch (e) {
      _didTriggerCapture = false;
      _isBusy = false;
      _setStatus('Failed to start. Try again.');
      await _restartTrackingIfNeeded();
    }
  }

 Future<void> _startVideoRecording() async {
  debugPrint('[Record] Attempting to start recording...');
  final controller = _controller;
  if (controller == null) {
    debugPrint('[Record] Controller is null, aborting');
    _didTriggerCapture = false;
    _isBusy = false;
    return;
  }

  await controller.lockCaptureOrientation(DeviceOrientation.portraitUp);

  //Log the actual preview size at the moment recording begins
  debugPrint(
      'Recording size: ${controller.value.previewSize}'
  );

  _setStatus('Recording...');
  if (mounted) {
    setState(() {
      _isRecording = true;
      _uploadProgress = 0.0;
    });
  }

  try {
    await controller.startVideoRecording();
    debugPrint('[Record] Recording started successfully');

    _recordTimer = Timer(const Duration(seconds: _recordDurationSeconds), () async {
      debugPrint('[Record] Timer fired, stopping...');
      if (!mounted) return;

      try {
        final XFile videoFile = await controller.stopVideoRecording();
        await controller.pausePreview();


        await Future.delayed(
          const Duration(milliseconds: 100),
        );

        await controller.resumePreview();

       
        final file = File(videoFile.path);

        if (!await file.exists()) {
          debugPrint("Video file missing");
          return;
        }

        final size = await file.length();
        debugPrint("Video size: $size");

        if (size == 0) {
          debugPrint("Video file is empty");
          _isBusy = false;
          _didTriggerCapture = false;
          _setStatus('Center your face inside the ring.');
          await _restartTrackingIfNeeded();
          await _autoStartRecording();
          return;
        }

        if (mounted) {
          setState(() {
            _isRecording = false;
            _uploadProgress = 0.5;
          });
        }
        await _uploadVideo(videoFile);
      } catch (e) {
        if (mounted) {
          setState(() => _isRecording = false);
          _setStatus('Recording failed. Try again.');
          _didTriggerCapture = false;
          _isBusy = false;
          await _restartTrackingIfNeeded();
        }
      }
    });
  } catch (e) {
    setState(() => _isRecording = false);
    _setStatus('Recording failed. Try again.');
    _didTriggerCapture = false;
    _isBusy = false;
    await _restartTrackingIfNeeded();
  }
}


  Future<void> _uploadVideo(XFile videoFile) async {
    _isUploading = true;
    _setStatus('Processing video...');
    if (mounted) setState(() => _uploadProgress = 0.6);

    try {
      final result = await _api.uploadEnrollment(
        draft: widget.draft,
        videoFile: File(videoFile.path),
        frameWidth: _lastFrameW,
        frameHeight: _lastFrameH,
      );

      // Clean up the temp video
      try { await File(videoFile.path).delete(); } catch (_) {}

      if (!mounted) return;

      _isUploading = false;
      _isBusy = false;

      if (result.ok) {
        setState(() => _uploadProgress = 1.0);
        _setStatus(result.message);
        await Navigator.of(context).pushAndRemoveUntil<void>(
          MaterialPageRoute<void>(
            builder: (_) => EnrollmentKeyResultScreen(
              username: widget.draft.username,
              hashkey: result.hashkey,
            ),
          ),
          (route) => route.isFirst,
        );
        return;
      }

      // Upload failed
      setState(() {
        _uploadProgress = 0.0;
        if (result.statusCode != 409) {
          _uploadErrorDetail = result.message;
        }
      });
      _setStatus(result.message);
      _didTriggerCapture = false;
      await _restartTrackingIfNeeded();
    } catch (e) {
      _didTriggerCapture = false;
      _isBusy = false;
      _isUploading = false;
      if (!mounted) return;
      final url = _api.endpointSync.isEmpty ? '(no URL)' : _api.endpointSync;
      setState(() {
        _uploadErrorDetail = 'Enrollment failed.\nPOST $url\n\n$e';
        _uploadProgress = 0.0;
      });
      _setStatus('Could not complete enrollment. Try again.');
      await _restartTrackingIfNeeded();
    }
  }

  Future<void> _restartTrackingIfNeeded() async {
    if (!mounted) return;
    final controller = _controller;
    if (controller == null || !controller.value.isInitialized || _isUploading) return;
    if (!_isBusy && !_isStreaming) {
      _consecutiveGoodFrames = 0;
      await _startFaceTracking();
      if (mounted) setState(() {});
    }
  }

  // ─── Build ──────

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.sizeOf(context);
    final previewHeight = size.height - _bottomPanelHeight;

    //Circle size
    final double ringSize = (size.width * 0.75).clamp(240.0, 340.0);
    final double ringCentreFromTop = previewHeight * 0.50;
    final double ringLeft         = (size.width - ringSize) / 2;
    final double ringTop          = ringCentreFromTop - ringSize / 2;
    const double tickRingMargin   = 20;

    return Scaffold(
      backgroundColor: Colors.transparent,
      bottomSheet: null,
      extendBodyBehindAppBar: true,
      extendBody: true,
      body: Stack(
        fit: StackFit.expand,
        children: [
          // 1. Transparent full-screen background
          const SizedBox.expand(),

          Positioned(
            left: 0,
            top: 0,
            right: 0,
            bottom: _bottomPanelHeight,
            child: _FullScreenCamera(
              controller: _controller,
              initializeFuture: _initializeFuture,
              error: _cameraError,
              isRecording: _isRecording,
              onRetry: _initializeCamera,
            ),
          ),

          // 2. Close button
          SafeArea(
            child: Align(
              alignment: Alignment.topLeft,
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: GestureDetector(
                  onTap: () => Navigator.of(context).popUntil((r) => r.isFirst),
                  child: Container(
                    width: 36,
                    height: 36,
                    decoration: const BoxDecoration(
                      color: Colors.black54,
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.close, color: Colors.white, size: 20),
                  ),
                ),
              ),
            ),
          ),

          // 3. Main Tick Ring - shows recording progress with green ticks
          //    - Shows normal ring during face detection
          //    - Turns green progressively during recording and upload
          Positioned(
            left:   ringLeft - tickRingMargin,
            top:    ringTop  - tickRingMargin,
            width:  ringSize + (tickRingMargin * 2),
            height: ringSize + (tickRingMargin * 2),
            child: TickRing(
              size:              ringSize + (tickRingMargin * 2),
              tickCount:         72,
              tickWidth:         4,
              tickLength:        14,
              gap:               0,
              tickColor:         const Color(0xFFBFC6CF),
              activeTickColor:   const Color(0xFF00E676),
              // During recording: show full green ring
              // During upload: show progress
              // Otherwise: no active ticks
              activeTickFraction: _isRecording ? 1.0 : _uploadProgress,
              child: const SizedBox.shrink(),
            ),
          ),
          // 4. Bottom panel
          Align(
            alignment: Alignment.bottomCenter,
            child: Container(
              width: double.infinity,
              height: _bottomPanelHeight,
              color: Colors.black,
              padding: const EdgeInsets.fromLTRB(24, 28, 24, 32),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'Look at the camera',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w600,
                      color: Colors.white,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _status,
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      fontSize: 14,
                      height: 1.4,
                      color: Color(0xFFBFC6CF),
                    ),
                  ),

                  if (_uploadErrorDetail != null && !_isUploading) ...[
                    const SizedBox(height: 10),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: const Color(0xFF2A1518),
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: const Color(0xFF5C2A32)),
                      ),
                      child: Text(
                        _uploadErrorDetail!,
                        style: const TextStyle(
                          fontSize: 14,
                          height: 1.45,
                          color: Color(0xFFE8D4D4),
                        ),
                      ),
                    ),
                  ],

                  const SizedBox(height: 20),

                  PrimaryButton(
                    label: _isBusy || _isUploading ? 'Please wait…' : 'Start over',
                    backgroundColor: (!_isBusy && !_isUploading)
                        ? AppTheme.accent
                        : const Color(0xFF2A313C),
                    onPressed: (_isBusy || _isUploading)
                        ? null
                        : () => Navigator.of(context)
                            .popUntil((route) => route.isFirst),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ─── Full-screen camera widget ────

class _FullScreenCamera extends StatelessWidget {
  const _FullScreenCamera({
    required this.controller,
    required this.initializeFuture,
    required this.error,
    required this.isRecording,
    required this.onRetry,
  });

  final CameraController? controller;
  final Future<void>?     initializeFuture;
  final String?           error;
  final bool              isRecording;
  final VoidCallback      onRetry;

  static const double _targetPreviewAspect = 3.0 / 4.0;

  @override
  Widget build(BuildContext context) {
    if (error != null) {
      return Container(
        color: const Color(0xFF1D2430),
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.camera_alt_rounded,
                    color: Color(0x88FFFFFF), size: 48),
                const SizedBox(height: 12),
                Text(
                  error!,
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                      color: Color(0xFFBFC6CF), fontSize: 15, height: 1.4),
                ),
                const SizedBox(height: 16),
                TextButton(
                  onPressed: () async {
                    final status = await Permission.camera.status;
                    if (status.isPermanentlyDenied) {
                      await openAppSettings();
                      return;
                    }
                    onRetry();
                  },
                  child: const Text('Enable camera'),
                ),
              ],
            ),
          ),
        ),
      );
    }

    final c    = controller;
    final init = initializeFuture;
    if (c == null || init == null) {
      return const SizedBox.expand();
    }

    return FutureBuilder<void>(
      future: init,
      builder: (context, snapshot) {
        if (snapshot.connectionState != ConnectionState.done ||
            !c.value.isInitialized) {
          return const Center(
            child: SizedBox(
              width: 34,
              height: 34,
              child: CircularProgressIndicator(strokeWidth: 3),
            ),
          );
        }

        final previewSize = c.value.previewSize;
        final previewAspect = previewSize == null
            ? _targetPreviewAspect
            : previewSize.height / previewSize.width;

        return SizedBox.expand(
          child: Transform(
            alignment: Alignment.center,
            transform: Matrix4.diagonal3Values(-1, 1, 1),
            child: FittedBox(
              fit: BoxFit.contain,
              child: SizedBox(
                width: 1000,
                height: 1000 / previewAspect,
                child: CameraPreview(c),
              ),
            ),
          ),
        );
      },
    );
  }
}

class _PlaceholderCircle extends StatelessWidget {
  const _PlaceholderCircle({required this.diameter});
  final double diameter;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: diameter,
      height: diameter,
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          colors: [Color(0xFF2B3646), Color(0xFF151B25)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
      ),
      child: const Center(
        child: SizedBox(
          width: 34,
          height: 34,
          child: CircularProgressIndicator(strokeWidth: 3),
        ),
      ),
    );
  }
}

class _CircularCutoutPainter extends CustomPainter {
  const _CircularCutoutPainter({
    required this.center,
    required this.radius,
  });

  final Offset center;
  final double radius;

  @override
  void paint(Canvas canvas, Size size) {
    final path = Path()
      ..addRect(Rect.fromLTWH(0, 0, size.width, size.height))
      ..addOval(Rect.fromCircle(center: center, radius: radius))
      ..fillType = PathFillType.evenOdd;

    canvas.drawPath(
      path,
      Paint()
        ..color = Colors.black.withOpacity(0.92)
        ..style = PaintingStyle.fill,
    );
  }

  @override
  bool shouldRepaint(_CircularCutoutPainter old) =>
      old.center != center || old.radius != radius;
}

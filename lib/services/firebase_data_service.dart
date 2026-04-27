import 'dart:io';
import 'dart:typed_data';

import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_storage/firebase_storage.dart';
import 'package:flutter/foundation.dart';

class FirebaseDataService {
  FirebaseDataService({
    FirebaseFirestore? firestore,
    FirebaseStorage? storage,
  })  : _firestore = firestore ?? FirebaseFirestore.instance,
        _storage = storage ?? FirebaseStorage.instance;

  final FirebaseFirestore _firestore;
  final FirebaseStorage _storage;

  bool get isReady => Firebase.apps.isNotEmpty;

  DocumentReference<Map<String, dynamic>> userDoc(String username) =>
      _firestore.collection('users').doc(username);

  CollectionReference<Map<String, dynamic>> _signUpCollection(String username) =>
      userDoc(username).collection('signups');

  CollectionReference<Map<String, dynamic>> _loginCollection(String username) =>
      userDoc(username).collection('logins');

  Future<void> saveUserProfile({
    required String username,
    required bool isLogin,
    Map<String, dynamic> extra = const {},
  }) async {
    if (!isReady) return;
    await userDoc(username).set({
      'username': username,
      'lastFlow': isLogin ? 'login' : 'signup',
      'updatedAt': FieldValue.serverTimestamp(),
      ..._sanitize(extra),
    }, SetOptions(merge: true));
  }

  Future<String?> tryUploadVideoFile({
    required String username,
    required File file,
    required String folder,
  }) async {
    if (!isReady) return null;

    try {
      final filename = file.uri.pathSegments.isNotEmpty
          ? file.uri.pathSegments.last
          : 'video.mp4';
      final path =
          '$folder/$username/${DateTime.now().millisecondsSinceEpoch}_$filename';
      final ref = _storage.ref().child(path);
      await ref.putFile(file);
      return ref.getDownloadURL();
    } catch (e, st) {
      debugPrint('[Firebase] Video upload failed: $e\n$st');
      return null;
    }
  }

  /// Uploads a single JPEG frame (as raw bytes) to Firebase Storage.
  /// Used to store a thumbnail frame extracted from the recorded video,
  /// for both signup ([folder] = 'signup_frames') and
  /// login ([folder] = 'login_frames').
  Future<String?> tryUploadFrameImage({
    required String username,
    required Uint8List imageBytes,
    required String folder,
    required String filename,
  }) async {
    if (!isReady) return null;

    try {
      final path =
          '$folder/$username/${DateTime.now().millisecondsSinceEpoch}_$filename';
      final ref = _storage.ref().child(path);
      await ref.putData(
        imageBytes,
        SettableMetadata(contentType: 'image/jpeg'),
      );
      final url = await ref.getDownloadURL();
      debugPrint('[Firebase] Frame uploaded: $url');
      return url;
    } catch (e, st) {
      debugPrint('[Firebase] Frame upload failed: $e\n$st');
      return null;
    }
  }

  Future<void> saveSignUpRecord({
    required String username,
    required bool success,
    required String hashkey,
    required String message,
    required double frameWidth,
    required double frameHeight,
    String? videoUrl,
    String? frameUrl,
    Map<String, dynamic> helperData = const {},  // ← add this
    String? backendRawBody,
    int? backendStatusCode,
    String? backendRequestUrl,
  }) async {
    if (!isReady) return;

    final payload = _sanitize({
      'username': username,
      'success': success,
      'hashkey': hashkey,
      'message': message,
      'frameWidth': frameWidth,
      'frameHeight': frameHeight,
      'videoUrl': videoUrl,
      'frameUrl': frameUrl,
      'helperData': helperData,   // ← add this
      'backendRawBody': backendRawBody,
      'backendStatusCode': backendStatusCode,
      'backendRequestUrl': backendRequestUrl,
      'createdAt': FieldValue.serverTimestamp(),
    });

    await saveUserProfile(
      username: username,
      isLogin: false,
      extra: {
        'lastSignupSuccess': success,
        'lastSignupHashkey': hashkey,
        'lastSignupVideoUrl': videoUrl,
        'lastSignupFrameUrl': frameUrl,
      },
    );

    await _signUpCollection(username).add(payload);
  }

  Future<void> saveLoginRecord({
    required String username,
    required bool success,
    required String hashkey,
    required String message,
    required double frameWidth,
    required double frameHeight,
    String? videoUrl,
    String? frameUrl,
     Map<String, dynamic> helperData = const {},  // ← add this
    String? backendRawBody,
    int? backendStatusCode,
    String? backendRequestUrl,
  }) async {
    if (!isReady) return;

    final payload = _sanitize({
      'username': username,
      'success': success,
      'hashkey': hashkey,
      'message': message,
      'frameWidth': frameWidth,
      'frameHeight': frameHeight,
      'videoUrl': videoUrl,
      'frameUrl': frameUrl,
      'helperData': helperData,           // ← add this
      'backendRawBody': backendRawBody,
      'backendStatusCode': backendStatusCode,
      'backendRequestUrl': backendRequestUrl,
      'createdAt': FieldValue.serverTimestamp(),
    });

    await saveUserProfile(
      username: username,
      isLogin: true,
      extra: {
        'lastLoginSuccess': success,
        'lastLoginHashkey': hashkey,
        'lastLoginVideoUrl': videoUrl,
        'lastLoginFrameUrl': frameUrl,
      },
    );

    await _loginCollection(username).add(payload);
  }

  Map<String, dynamic> _sanitize(Map<String, dynamic> data) {
    final out = <String, dynamic>{};
    data.forEach((key, value) {
      if (value == null) return;
      if (value is Map<String, dynamic>) {
        out[key] = _sanitize(value);
      } else if (value is Iterable) {
        out[key] = value.where((item) => item != null).map((item) {
          if (item is Map<String, dynamic>) return _sanitize(item);
          return item;
        }).toList();
      } else {
        out[key] = value;
      }
    });
    return out;
  }
}

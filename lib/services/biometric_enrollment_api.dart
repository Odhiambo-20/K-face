import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../models/enrollment_draft.dart';

class EnrollmentUploadResult {
  const EnrollmentUploadResult({
    required this.ok,
    required this.message,
    required this.hashkey,
    this.rawBody,
    this.requestUrl,
    this.statusCode,
  });

  final bool ok;
  final String message;
  final String hashkey;
  final String? rawBody;
  final String? requestUrl;
  final int? statusCode;
}

const _kEndpointPrefKey = 'enrollment_api_url';
// TEMP TODAY: ngrok public tunnel URL.
// Keep ngrok and the local backend running while remote testers use the app.

const _kDevFallbackUrl =
    'https://unphilosophic-madelaine-monomorphic.ngrok-free.dev/enroll';

// ORIGINAL CLOUD RUN ENDPOINT - restore after today's ngrok test.
// const _kDevFallbackUrl =
//     'https://kface-backend-53376040704.us-central1.run.app/enroll';

const _kCompileTimeEndpoint = String.fromEnvironment(
  'ENROLLMENT_API_URL',
  defaultValue: _kDevFallbackUrl,
);

// Required header to bypass ngrok's browser warning page on free tier
const _kNgrokHeaders = {
  'ngrok-skip-browser-warning': 'true',
};

// TEMP TODAY: force the ngrok endpoint even if an older endpoint was saved
// in SharedPreferences on the phone.
const _kForceTodayNgrokEndpoint = true;

class BiometricEnrollmentApi {
  BiometricEnrollmentApi({http.Client? client})
      : _client = client ?? http.Client();

  final http.Client _client;
  String _cachedEndpoint = _kCompileTimeEndpoint;

  Future<String> getEndpoint() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(_kEndpointPrefKey) ?? '';
    if (_kForceTodayNgrokEndpoint) {
      debugPrint(
          '[EnrollmentApi] TEMP TODAY using ngrok endpoint: $_kCompileTimeEndpoint');
      return _kCompileTimeEndpoint;
    }

    // ORIGINAL SAVED-ENDPOINT BEHAVIOR - active again when
    // _kForceTodayNgrokEndpoint is false.
    if (saved.isNotEmpty) {
      debugPrint('[EnrollmentApi] Using saved endpoint: $saved');
      return saved;
    }
    debugPrint(
        '[EnrollmentApi] Using compile-time endpoint: $_kCompileTimeEndpoint');
    return _kCompileTimeEndpoint;
  }

  static Future<void> setEndpoint(String url) async {
    final prefs = await SharedPreferences.getInstance();
    if (url.isEmpty) {
      await prefs.remove(_kEndpointPrefKey);
    } else {
      await prefs.setString(_kEndpointPrefKey, url.trim());
    }
  }

  String get endpointSync => _cachedEndpoint;

  Future<void> loadEndpoint() async {
    _cachedEndpoint = await getEndpoint();
    debugPrint('[EnrollmentApi] Loaded endpoint: $_cachedEndpoint');
  }

  Future<String> _getBaseUrl() async {
    final enrollUrl = await getEndpoint();
    _cachedEndpoint = enrollUrl;
    return enrollUrl.endsWith('/enroll')
        ? enrollUrl.substring(0, enrollUrl.length - '/enroll'.length)
        : enrollUrl;
  }

  Future<bool> checkUsernameAvailable(String username) async {
    final baseUrl = await _getBaseUrl();
    final uri = Uri.parse('$baseUrl/check-username')
        .replace(queryParameters: {'username': username});

    debugPrint('[EnrollmentApi] Checking username availability: $uri');

    try {
      final response =
          await _client.get(uri).timeout(const Duration(seconds: 10));

      debugPrint(
          '[EnrollmentApi] Username check status: ${response.statusCode}');
      debugPrint('[EnrollmentApi] Username check body: ${response.body}');

      if (response.statusCode == 200) {
        final body = jsonDecode(response.body) as Map<String, dynamic>;
        return body['available'] == true;
      }
      throw Exception('Username check failed: HTTP ${response.statusCode}');
    } catch (e) {
      debugPrint('[EnrollmentApi] Username check error: $e');
      rethrow;
    }
  }

  Future<bool> checkUsernameExists(String username) async {
    final baseUrl = await _getBaseUrl();
    final uri = Uri.parse('$baseUrl/check-login-username')
        .replace(queryParameters: {'username': username});

    debugPrint('[EnrollmentApi] Checking login username: $uri');

    try {
      final response =
          await _client.get(uri).timeout(const Duration(seconds: 10));

      debugPrint(
          '[EnrollmentApi] Login username status: ${response.statusCode}');
      debugPrint('[EnrollmentApi] Login username body: ${response.body}');

      if (response.statusCode == 200) {
        final body = jsonDecode(response.body) as Map<String, dynamic>;
        return body['exists'] == true;
      }
      throw Exception(
          'Login username check failed: HTTP ${response.statusCode}');
    } catch (e) {
      debugPrint('[EnrollmentApi] Login username check error: $e');
      rethrow;
    }
  }

  Future<EnrollmentUploadResult> uploadEnrollment({
    required EnrollmentDraft draft,
    required File videoFile,
    double frameWidth = 0,
    double frameHeight = 0,
  }) async {
    final url = await getEndpoint();
    return _uploadVideoRequest(
      url: url,
      draft: draft,
      videoFile: videoFile,
      frameWidth: frameWidth,
      frameHeight: frameHeight,
      successMessage: 'Enrollment successful!',
      duplicateMessage: 'You already have an account. Please log in.',
    );
  }

  Future<EnrollmentUploadResult> uploadLogin({
    required EnrollmentDraft draft,
    required File videoFile,
    double frameWidth = 0,
    double frameHeight = 0,
  }) async {
    final baseUrl = await _getBaseUrl();
    return _uploadVideoRequest(
      url: '$baseUrl/login',
      draft: draft,
      videoFile: videoFile,
      frameWidth: frameWidth,
      frameHeight: frameHeight,
      successMessage: 'Login successful!',
      duplicateMessage:
          'This account already exists. Please sign up with a different username.',
    );
  }

  Future<EnrollmentUploadResult> _uploadVideoRequest({
    required String url,
    required EnrollmentDraft draft,
    required File videoFile,
    required double frameWidth,
    required double frameHeight,
    required String successMessage,
    required String duplicateMessage,
  }) async {
    _cachedEndpoint = url;

    debugPrint('[EnrollmentApi]');
    debugPrint('[EnrollmentApi] ATTEMPTING UPLOAD');
    debugPrint('[EnrollmentApi] URL: $url');
    debugPrint('[EnrollmentApi] Username: ${draft.username}');
    debugPrint(
        '[EnrollmentApi] Frame size: ${frameWidth.toInt()} x ${frameHeight.toInt()}');
    debugPrint('[EnrollmentApi] Video path: ${videoFile.path}');

    if (!await videoFile.exists()) {
      debugPrint('[EnrollmentApi] ERROR: Video file does not exist!');
      return const EnrollmentUploadResult(
        ok: false,
        hashkey: '',
        message: 'Video file not found',
        requestUrl: null,
        statusCode: null,
      );
    }

    final fileSize = await videoFile.length();
    debugPrint(
        '[EnrollmentApi] Video size: ${(fileSize / 1024).toStringAsFixed(2)} KB');

    if (url.isEmpty) {
      debugPrint('[EnrollmentApi] ERROR: No URL configured');
      return const EnrollmentUploadResult(
        ok: false,
        hashkey: '',
        message: 'No upload URL configured. Please check connection settings.',
        requestUrl: null,
        statusCode: null,
      );
    }

    // Determine a safe filename with a valid video extension.
    // The OS-assigned temp path often has no extension or ".temp",
    // which causes OpenCV on the server to fail. We force .mp4 or .mov.
    const _allowedVideoExts = {'.mp4', '.mov'};
    final rawName = videoFile.uri.pathSegments.isNotEmpty
        ? videoFile.uri.pathSegments.last
        : '';
    final rawExt = rawName.contains('.')
        ? rawName.substring(rawName.lastIndexOf('.')).toLowerCase()
        : '';
    final safeExt = _allowedVideoExts.contains(rawExt) ? rawExt : '.mp4';
    final filename = 'enrollment$safeExt';

    debugPrint(
        '[EnrollmentApi] Upload filename: $filename (raw path was: $rawName)');

    final request = http.MultipartRequest('POST', Uri.parse(url))
      ..headers.addAll(_kNgrokHeaders) // bypass ngrok browser warning
      ..fields['username'] = draft.username
      ..fields['frame_width'] = frameWidth.toInt().toString()
      ..fields['frame_height'] = frameHeight.toInt().toString();

    request.files.add(
      await http.MultipartFile.fromPath(
        'video',
        videoFile.path,
        filename: filename,
      ),
    );

    debugPrint('[EnrollmentApi] Sending request...');
    final stopwatch = Stopwatch()..start();

    try {
      final streamed = await request.send().timeout(
        const Duration(seconds: 300),
        onTimeout: () {
          throw Exception('Connection timeout after 60 seconds');
        },
      );

      final response = await http.Response.fromStream(streamed);
      stopwatch.stop();

      debugPrint('[EnrollmentApi] Response status: ${response.statusCode}');
      debugPrint(
          '[EnrollmentApi] Response time: ${stopwatch.elapsedMilliseconds} ms');
      debugPrint('[EnrollmentApi] Response body: ${response.body}');

      final ok = response.statusCode >= 200 && response.statusCode < 300;
      String message =
          ok ? successMessage : 'Upload failed (HTTP ${response.statusCode})';
      String hashkey = '';
      // ← ADD THIS:
      debugPrint('[EnrollmentApi] Full response body: ${response.body}');

      if (response.body.isNotEmpty) {
        try {
          final decoded = jsonDecode(response.body) as Map<String, dynamic>;
          final serverHashkey = decoded['hashkey']?.toString() ?? '';
          if (serverHashkey.isNotEmpty) {
            hashkey = serverHashkey;
            debugPrint('[EnrollmentApi] Hashkey received: $hashkey');
          }
          final serverMessage = decoded['message']?.toString() ?? '';
          if (serverMessage.isNotEmpty) {
            message = serverMessage;
          }
          if (!ok && response.statusCode == 409) {
            message = duplicateMessage;
          } else if (!ok) {
            // FastAPI returns errors as {"detail": "..."}
            final detail = decoded['detail']?.toString() ?? '';
            if (detail.isNotEmpty) message = detail;
          }
        } catch (e) {
          debugPrint('[EnrollmentApi] JSON parse error: $e');
          debugPrint(
              '[EnrollmentApi] Raw body that failed: "${response.body}"');
        }
      }

      debugPrint('[EnrollmentApi] Result - ok: $ok, message: $message');
      debugPrint('[EnrollmentApi] ');

      return EnrollmentUploadResult(
        ok: ok,
        message: message,
        hashkey: hashkey,
        rawBody: response.body,
        requestUrl: url,
        statusCode: response.statusCode,
      );
    } catch (e) {
      debugPrint('[EnrollmentApi] ERROR: $e');
      debugPrint('[EnrollmentApi] ');
      return EnrollmentUploadResult(
        ok: false,
        hashkey: '',
        message:
            'Connection failed: $e\n\nMake sure the backend is running at:\n$url',
        requestUrl: url,
        statusCode: null,
      );
    }
  }
}

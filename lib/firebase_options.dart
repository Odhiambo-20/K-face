import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/foundation.dart';

/// Replace these placeholder values with your real Firebase app values,
/// or overwrite this file by running `flutterfire configure`.
class DefaultFirebaseOptions {
  static FirebaseOptions get currentPlatform {
    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
        return android;
      case TargetPlatform.iOS:
        return ios;
      default:
        throw UnsupportedError(
          'DefaultFirebaseOptions are not configured for this platform.',
        );
    }
  }

  static bool get isConfigured {
    final options = currentPlatform;
    return options.apiKey.isNotEmpty &&
        options.appId.isNotEmpty &&
        options.messagingSenderId.isNotEmpty &&
        options.projectId.isNotEmpty;
  }

  static const FirebaseOptions android = FirebaseOptions(
    apiKey: 'AIzaSyBHQOajqN5aSJpGV2pnoykCT0QgwL4KSIY',
    appId: '1:53376040704:android:444a10a17e02c306a77de5',
    messagingSenderId: '53376040704',
    projectId: 'wrytte-app-3c029',
    storageBucket: 'wrytte-app-3c029.firebasestorage.app',
  );

  static const FirebaseOptions ios = FirebaseOptions(
    apiKey: 'AIzaSyD-wLIjJd_rCT_jBOUicmdnwbNrTPiLrLw',
    appId: '1:53376040704:ios:3a9596969f2ef8dca77de5',
    messagingSenderId: '53376040704',
    projectId: 'wrytte-app-3c029',
    storageBucket: 'wrytte-app-3c029.firebasestorage.app',
    iosBundleId: 'com.example.ux',
  );

}
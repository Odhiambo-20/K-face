import 'package:flutter/material.dart';

import '../models/enrollment_draft.dart';
import '../theme/app_theme.dart';
import '../widgets/app_background.dart';
import '../widgets/primary_button.dart';
import '../widgets/tick_ring.dart';
import 'look_at_camera_screen.dart';

class HowToSetupKfaceScreen extends StatelessWidget {
  const HowToSetupKfaceScreen({
    super.key,
    required this.draft,
  });

  static const route = '/how-to';
  final EnrollmentDraft draft;

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.sizeOf(context);
    final horizontal = (size.width * 0.08).clamp(18, 40).toDouble();
    final title = draft.isLogin ? 'How to Log In to Kface' : 'How to Set Up Kface';
    final description = draft.isLogin
        ? 'Position your face in the camera frame, then look directly at the camera to recover your key.'
        : 'First, position your face in the\ncamera frame. Then look at the camera';

    return AppBackground(
      child: Scaffold(
        body: SafeArea(
          child: SingleChildScrollView(
            padding: EdgeInsets.symmetric(horizontal: horizontal),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                Align(
                  alignment: Alignment.centerLeft,
                  child: IconButton(
                    icon: const Icon(Icons.arrow_back, color: Colors.white),
                    onPressed: () => Navigator.of(context).maybePop(),
                  ),
                ),
                const SizedBox(height: 48),
                TickRing(
                  size: (size.width * 0.62).clamp(220, 320).toDouble(),
                  tickCount: 60,
                  tickWidth: 4,
                  tickLength: 14,
                  gap: 10,
                  tickColor: const Color(0xFFBFC6CF),
                  child: Container(
                    width: (size.width * 0.32).clamp(120, 170).toDouble(),
                    height: (size.width * 0.32).clamp(120, 170).toDouble(),
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: Colors.transparent,
                      border: Border.all(
                        color: const Color(0xFFBFC6CF),
                        width: 5,
                      ),
                    ),
                    child: const Icon(
                      Icons.tag_faces_rounded,
                      color: Color(0xFFBFC6CF),
                      size: 70,
                    ),
                  ),
                ),
                const SizedBox(height: 24),
                Text(
                  title,
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    fontSize: 26,
                    fontWeight: FontWeight.w700,
                    color: AppTheme.textPrimary,
                  ),
                ),
                const SizedBox(height: 10),
                Text(
                  description,
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    fontSize: 18,
                    height: 1.35,
                    fontWeight: FontWeight.w400,
                    color: Color(0xFF7F8894),
                  ),
                ),
                const SizedBox(height: 60),
                PrimaryButton(
                  label: 'Get Started',
                  onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) => LookAtCameraScreen(draft: draft),
                    ),
                  ),
                ),
                const SizedBox(height: 18),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

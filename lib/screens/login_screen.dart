import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import '../models/enrollment_draft.dart';
import 'how_to_setup_kface_screen.dart';
import 'sign_up_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  static const route = '/';

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _usernameController = TextEditingController();
  final _usernameFocus = FocusNode();
  String? _error;

  static const _black = Color(0xFF000000);
  static const _fieldFill = Color(0xFF2C2C2E);
  static const _hintGrey = Color(0xFF8E8E93);
  static const _linkBlue = Color(0xFF0A84FF);
  static const _buttonIdle = Color(0xFF2C2C2E);
  static const _buttonLabelIdle = Color(0xFFAEAEB2);

  @override
  void dispose() {
    _usernameController.dispose();
    _usernameFocus.dispose();
    super.dispose();
  }

  void _goNext() {
    final username = _usernameController.text.trim();
    if (username.isEmpty) {
      setState(() {
        _error = 'Enter a username to continue.';
      });
      return;
    }

    setState(() {
      _error = null;
    });

    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => HowToSetupKfaceScreen(
          draft: EnrollmentDraft(
            username: username,
            flow: AuthFlow.logIn,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.paddingOf(context).bottom;
    final canNext = _usernameController.text.trim().isNotEmpty;

    return Scaffold(
      backgroundColor: _black,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 28),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SizedBox(height: 48),
              const Text(
                'Kface',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 36,
                  fontWeight: FontWeight.w700,
                  letterSpacing: -0.5,
                ),
              ),
              const SizedBox(height: 10),
              const Text(
                'Enter your username to log in',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: _hintGrey,
                  fontSize: 15,
                  fontWeight: FontWeight.w400,
                ),
              ),
              const SizedBox(height: 40),
              const Text(
                'Log in',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 20,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _usernameController,
                focusNode: _usernameFocus,
                onChanged: (_) => setState(() {}),
                autocorrect: false,
                textInputAction: TextInputAction.done,
                onSubmitted: (_) {
                  if (canNext) _goNext();
                },
                keyboardType: TextInputType.text,
                style: const TextStyle(color: Colors.white, fontSize: 17),
                cursorColor: _linkBlue,
                decoration: InputDecoration(
                  filled: true,
                  fillColor: _fieldFill,
                  hintText: 'Username',
                  hintStyle: const TextStyle(color: _hintGrey, fontSize: 17),
                  prefixIcon: const Padding(
                    padding: EdgeInsets.only(left: 14, right: 6),
                    child: Align(
                      widthFactor: 1,
                      alignment: Alignment.centerLeft,
                      child: Text(
                        '@',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 17,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ),
                  ),
                  prefixIconConstraints:
                      const BoxConstraints(minWidth: 0, minHeight: 0),
                  contentPadding: const EdgeInsets.symmetric(
                    vertical: 16,
                    horizontal: 0,
                  ),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: BorderSide.none,
                  ),
                ),
              ),
              if (_error != null) ...[
                const SizedBox(height: 10),
                Text(
                  _error!,
                  style: const TextStyle(
                    color: Color(0xFFE6A6A6),
                    fontSize: 14,
                  ),
                ),
              ],
              const SizedBox(height: 22),
              const Text(
                'Or',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: _hintGrey,
                  fontSize: 14,
                ),
              ),
              const SizedBox(height: 8),
              Center(
                child: Text.rich(
                  TextSpan(
                    text: 'Sign up',
                    style: const TextStyle(
                      color: _linkBlue,
                      fontSize: 16,
                      fontWeight: FontWeight.w500,
                    ),
                    recognizer: TapGestureRecognizer()
                      ..onTap = () => Navigator.of(context).push(
                            MaterialPageRoute<void>(
                              builder: (_) => const SignUpScreen(),
                            ),
                          ),
                  ),
                ),
              ),
              const Spacer(),
              SizedBox(
                width: double.infinity,
                height: 52,
                child: FilledButton(
                  onPressed: canNext ? _goNext : null,
                  style: FilledButton.styleFrom(
                    backgroundColor: _buttonIdle,
                    disabledBackgroundColor: _buttonIdle,
                    foregroundColor:
                        canNext ? Colors.white : _buttonLabelIdle,
                    disabledForegroundColor: _buttonLabelIdle,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                    elevation: 0,
                  ),
                  child: const Text(
                    'Next',
                    style: TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ),
              SizedBox(height: 16 + bottomInset),
            ],
          ),
        ),
      ),
    );
  }
}

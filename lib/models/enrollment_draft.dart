enum AuthFlow {
  signUp,
  logIn,
}

class EnrollmentDraft {
  const EnrollmentDraft({
    required this.username,
    this.flow = AuthFlow.signUp,
  });

  final String username;
  final AuthFlow flow;

  bool get isLogin => flow == AuthFlow.logIn;
  bool get isSignUp => flow == AuthFlow.signUp;
}

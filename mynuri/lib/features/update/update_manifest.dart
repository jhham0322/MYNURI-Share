import 'dart:convert';

/// Drive에 올린 latest.json의 내용을 표현한다.
class UpdateManifest {
  const UpdateManifest({
    required this.version,
    required this.build,
    required this.fileName,
    required this.fileId,
    required this.sha256,
    required this.size,
    this.notes,
    this.channel,
  });

  final String version;
  final int build;
  final String fileName;
  final String fileId;
  final String sha256;
  final int size;
  final String? notes;
  final String? channel;

  /// 형식이 맞지 않으면 null을 돌려준다. 예외를 던져 확인 흐름을 끊지 않는다.
  static UpdateManifest? tryParse(String body) {
    Object? value;
    try {
      value = jsonDecode(body);
    } catch (_) {
      return null;
    }
    if (value is! Map) return null;
    final version = value['version'];
    final build = value['build'];
    final fileName = value['file'];
    final fileId = value['fileId'];
    final channel = value['channel'];
    final hash = value['sha256'];
    final size = value['size'];
    if (version is! String || version.trim().isEmpty) return null;
    if (build is! int || build < 0) return null;
    if (fileName is! String || !_isSafeFileName(fileName)) return null;
    if ((fileId is! String || fileId.isEmpty) &&
        channel != 'nuri' && channel != 'assistant') return null;
    if (hash is! String || !RegExp(r'^[0-9a-fA-F]{64}$').hasMatch(hash)) {
      return null;
    }
    if (size is! int || size <= 0) return null;
    final notes = value['notes'];
    return UpdateManifest(
      version: version.trim(),
      build: build,
      fileName: fileName,
      fileId: fileId is String ? fileId : '',
      sha256: hash.toLowerCase(),
      size: size,
      notes: notes is String ? notes : null,
      channel: channel is String ? channel : null,
    );
  }

  bool isNewerThan({
    required String currentVersion,
    required int currentBuild,
  }) {
    if (build != currentBuild) return build > currentBuild;
    return compareVersions(version, currentVersion) > 0;
  }

  /// Drive의 공개 파일 내려받기 주소. 대용량 파일 확인 페이지를 건너뛰도록 confirm을 붙인다.
  Uri get downloadUri => Uri.https(
    'drive.usercontent.google.com',
    '/download',
    {'id': fileId, 'export': 'download', 'confirm': 't'},
  );
}

/// 점으로 나눈 숫자를 차례로 비교한다. 숫자가 아닌 부분은 0으로 본다.
int compareVersions(String a, String b) {
  final left = _parts(a);
  final right = _parts(b);
  final length = left.length > right.length ? left.length : right.length;
  for (var i = 0; i < length; i++) {
    final l = i < left.length ? left[i] : 0;
    final r = i < right.length ? right[i] : 0;
    if (l != r) return l.compareTo(r);
  }
  return 0;
}

List<int> _parts(String value) => value
    .split('.')
    .map((part) => int.tryParse(RegExp(r'^\d+').stringMatch(part) ?? '') ?? 0)
    .toList();

bool _isSafeFileName(String name) =>
    name.isNotEmpty &&
    !name.contains('/') &&
    !name.contains(r'\') &&
    !name.contains('..');

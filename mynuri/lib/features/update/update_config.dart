import 'package:flutter/foundation.dart';

/// 배포 스크립트가 latest.json을 올린 뒤 받은 Drive 파일 ID를 넣는다.
///
/// 비어 있으면 업데이트 확인을 건너뛴다. 검증할 때는
/// `--dart-define=UPDATE_MANIFEST_URL=...`로 주소를 직접 지정할 수 있다.
const String updateManifestFileId = String.fromEnvironment(
  'UPDATE_MANIFEST_ID',
  defaultValue: '',
);

const String _overrideUrl = String.fromEnvironment('UPDATE_MANIFEST_URL');
const String _debugDownloadUrl = String.fromEnvironment('UPDATE_DEBUG_APK_URL');

/// 디버그 APK에서만 로컬 업데이트 시험 서버를 사용할 수 있다.
Uri? debugUpdateDownloadUri() {
  if (!kDebugMode || _debugDownloadUrl.isEmpty) return null;
  final uri = Uri.tryParse(_debugDownloadUrl);
  if (uri == null || uri.scheme != 'http' || uri.host != '127.0.0.1') {
    return null;
  }
  return uri;
}

/// 버전 정보를 읽을 주소. 설정되지 않았으면 null이다.
Uri? updateManifestUri() {
  if (_overrideUrl.isNotEmpty) return Uri.tryParse(_overrideUrl);
  if (updateManifestFileId.isEmpty) return null;
  return Uri.https('drive.google.com', '/uc', {
    'export': 'download',
    'id': updateManifestFileId,
  });
}

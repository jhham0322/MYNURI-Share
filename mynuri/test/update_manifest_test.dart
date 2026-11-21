import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:mynuri/features/update/update_manifest.dart';

Map<String, Object> validJson() => {
  'version': '1.0.1',
  'build': 2,
  'file': 'mynuri_1.0.1+2_20261117.apk',
  'fileId': 'abc123',
  'sha256': List.filled(64, 'a').join(),
  'size': 1024,
};

void main() {
  test('올바른 manifest를 읽는다', () {
    final manifest = UpdateManifest.tryParse(jsonEncode(validJson()));
    expect(manifest, isNotNull);
    expect(manifest!.version, '1.0.1');
    expect(manifest.build, 2);
    expect(manifest.fileName, 'mynuri_1.0.1+2_20261117.apk');
  });

  test('형식이 잘못된 manifest는 null이다', () {
    expect(UpdateManifest.tryParse('not json'), isNull);
    expect(UpdateManifest.tryParse('[]'), isNull);

    final badHash = validJson()..['sha256'] = 'xyz';
    expect(UpdateManifest.tryParse(jsonEncode(badHash)), isNull);

    final badSize = validJson()..['size'] = 0;
    expect(UpdateManifest.tryParse(jsonEncode(badSize)), isNull);

    final badBuild = validJson()..['build'] = '2';
    expect(UpdateManifest.tryParse(jsonEncode(badBuild)), isNull);
  });

  test('경로가 섞인 파일 이름은 거부한다', () {
    for (final name in ['../evil.apk', 'a/b.apk', r'a\b.apk']) {
      final json = validJson()..['file'] = name;
      expect(UpdateManifest.tryParse(jsonEncode(json)), isNull);
    }
  });

  test('버전 문자열을 숫자 단위로 비교한다', () {
    expect(compareVersions('1.0.1', '1.0.0'), greaterThan(0));
    expect(compareVersions('1.0.0', '1.0.1'), lessThan(0));
    expect(compareVersions('1.0.10', '1.0.9'), greaterThan(0));
    expect(compareVersions('1.0', '1.0.0'), 0);
    expect(compareVersions('1.1', '1.0.9'), greaterThan(0));
  });

  test('빌드 번호를 먼저 비교한다', () {
    const manifest = UpdateManifest(
      version: '1.0.0',
      build: 3,
      fileName: 'a.apk',
      fileId: 'id',
      sha256:
          'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      size: 10,
    );
    expect(
      manifest.isNewerThan(currentVersion: '9.9.9', currentBuild: 2),
      isTrue,
    );
    expect(
      manifest.isNewerThan(currentVersion: '1.0.0', currentBuild: 3),
      isFalse,
    );
    expect(
      manifest.isNewerThan(currentVersion: '0.9.0', currentBuild: 3),
      isTrue,
    );
  });
}

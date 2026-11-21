import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mynuri/features/update/update_manifest.dart';
import 'package:mynuri/features/update/update_service.dart';

UpdateManifest manifestFor(
  List<int> bytes, {
  String fileName = 'mynuri.apk',
  int? size,
  String? hash,
}) => UpdateManifest(
  version: '1.0.1',
  build: 2,
  fileName: fileName,
  fileId: 'test',
  sha256: hash ?? sha256.convert(bytes).toString(),
  size: size ?? bytes.length,
);

Future<HttpServer> serve(List<int> bytes) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  server.listen((request) async {
    request.response.add(bytes);
    await request.response.close();
  });
  return server;
}

void main() {
  test('manifest를 읽고 내려받은 파일을 검증한다', () async {
    final bytes = utf8.encode('apk-content');
    final manifest = manifestFor(bytes);
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((request) async {
      final response = request.response;
      if (request.uri.path == '/latest.json') {
        response.headers.contentType = ContentType.json;
        response.write(
          jsonEncode({
            'version': manifest.version,
            'build': manifest.build,
            'file': manifest.fileName,
            'fileId': manifest.fileId,
            'sha256': manifest.sha256,
            'size': manifest.size,
          }),
        );
      } else {
        response.add(bytes);
      }
      await response.close();
    });

    final service = UpdateService();
    final directory = await Directory.systemTemp.createTemp('nuri_update');
    addTearDown(() async {
      service.close();
      await server.close(force: true);
      await directory.delete(recursive: true);
    });

    final base = Uri.parse('http://127.0.0.1:${server.port}');
    final fetched = await service.fetchManifest(base.resolve('/latest.json'));
    expect(fetched, isNotNull);
    expect(
      fetched!.isNewerThan(currentVersion: '1.0.0', currentBuild: 1),
      isTrue,
    );

    var received = 0;
    final file = await service.download(
      fetched,
      directory: directory,
      source: base.resolve('/download'),
      onProgress: (value, _) => received = value,
    );
    expect(await file.readAsString(), 'apk-content');
    expect(received, bytes.length);
  });

  test('크기가 다르면 받은 파일을 지우고 실패한다', () async {
    final bytes = utf8.encode('apk-content');
    final server = await serve(bytes);
    final service = UpdateService();
    final directory = await Directory.systemTemp.createTemp('nuri_update');
    addTearDown(() async {
      service.close();
      await server.close(force: true);
      await directory.delete(recursive: true);
    });

    final manifest = manifestFor(bytes, size: bytes.length + 1);
    await expectLater(
      service.download(
        manifest,
        directory: directory,
        source: Uri.parse('http://127.0.0.1:${server.port}/download'),
      ),
      throwsA(isA<FormatException>()),
    );
    expect(
      await File('${directory.path}/${manifest.fileName}').exists(),
      isFalse,
    );
  });

  test('해시가 다르면 받은 파일을 지우고 실패한다', () async {
    final bytes = utf8.encode('apk-content');
    final server = await serve(bytes);
    final service = UpdateService();
    final directory = await Directory.systemTemp.createTemp('nuri_update');
    addTearDown(() async {
      service.close();
      await server.close(force: true);
      await directory.delete(recursive: true);
    });

    final manifest = manifestFor(bytes, hash: List.filled(64, 'b').join());
    await expectLater(
      service.download(
        manifest,
        directory: directory,
        source: Uri.parse('http://127.0.0.1:${server.port}/download'),
      ),
      throwsA(isA<FormatException>()),
    );
    expect(
      await File('${directory.path}/${manifest.fileName}').exists(),
      isFalse,
    );
  });

  test('선언된 크기를 초과하면 다운로드를 중단하고 파일을 지운다', () async {
    final bytes = utf8.encode('apk-content');
    final server = await serve(bytes);
    final service = UpdateService();
    final directory = await Directory.systemTemp.createTemp('nuri_update');
    addTearDown(() async {
      service.close();
      await server.close(force: true);
      await directory.delete(recursive: true);
    });

    final manifest = manifestFor(bytes, size: 1);
    await expectLater(
      service.download(
        manifest,
        directory: directory,
        source: Uri.parse('http://127.0.0.1:${server.port}/download'),
      ),
      throwsA(isA<FormatException>()),
    );
    expect(
      await File('${directory.path}/${manifest.fileName}').exists(),
      isFalse,
    );
  });
}

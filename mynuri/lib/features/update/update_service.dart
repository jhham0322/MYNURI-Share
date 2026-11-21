import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';

import 'update_manifest.dart';

/// Drive의 버전 정보를 읽고 APK를 내려받아 검증한다.
class UpdateService {
  UpdateService({HttpClient? client})
    : _client =
          client ??
          (HttpClient()..connectionTimeout = const Duration(seconds: 10));

  final HttpClient _client;

  Future<UpdateManifest?> fetchManifest(Uri url) async {
    final request = await _client
        .getUrl(url)
        .timeout(const Duration(seconds: 10));
    final response = await request.close().timeout(const Duration(seconds: 10));
    if (response.statusCode != 200) {
      throw HttpException('버전 정보를 받지 못했습니다 (${response.statusCode})');
    }
    final body = await utf8.decoder
        .bind(response)
        .join()
        .timeout(const Duration(seconds: 10));
    return UpdateManifest.tryParse(body);
  }

  /// 내려받은 파일의 크기와 SHA-256이 manifest와 다르면 파일을 지우고 예외를 던진다.
  Future<File> download(
    UpdateManifest manifest, {
    required Directory directory,
    Uri? source,
    void Function(int received, int total)? onProgress,
  }) async {
    await directory.create(recursive: true);
    final target = File('${directory.path}/${manifest.fileName}');
    if (await target.exists()) {
      if (await target.length() == manifest.size &&
          (await sha256.bind(target.openRead()).first).toString() == manifest.sha256) {
        onProgress?.call(manifest.size, manifest.size);
        return target;
      }
      await target.delete();
    }

    final request = await _client
        .getUrl(source ?? manifest.downloadUri)
        .timeout(const Duration(seconds: 15));
    final response = await request.close().timeout(const Duration(seconds: 15));
    if (response.statusCode != 200) {
      throw HttpException('APK를 받지 못했습니다 (${response.statusCode})');
    }
    final mimeType = response.headers.contentType?.mimeType;
    if (mimeType == 'text/html') {
      throw const FormatException('Drive가 확인 페이지를 반환했습니다');
    }

    try {
      final sink = target.openWrite();
      var received = 0;
      try {
        await for (final chunk in response.timeout(
          const Duration(seconds: 30),
        )) {
          received += chunk.length;
          if (received > manifest.size) {
            throw const FormatException('내려받은 파일 크기가 다릅니다');
          }
          sink.add(chunk);
          onProgress?.call(received, manifest.size);
        }
      } finally {
        await sink.close();
      }

      if (received != manifest.size) {
        throw const FormatException('내려받은 파일 크기가 다릅니다');
      }
      final digest = await sha256.bind(target.openRead()).first;
      if (digest.toString() != manifest.sha256) {
        throw const FormatException('파일 검증에 실패했습니다');
      }
    } catch (_) {
      if (await target.exists()) await target.delete();
      rethrow;
    }
    return target;
  }

  void close() => _client.close(force: true);
}

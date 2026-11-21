import 'dart:convert';
import 'dart:io';
import 'dart:math';

Uri? parsePcAddress(String input) {
  final raw = input.trim();
  if (raw.isEmpty) return null;
  final uri = Uri.tryParse(raw.contains('://') ? raw : 'http://$raw');
  if (uri == null ||
      !['http', 'https'].contains(uri.scheme) ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      (uri.path.isNotEmpty && uri.path != '/') ||
      uri.hasQuery ||
      uri.hasFragment) {
    return null;
  }
  return uri.hasPort ? uri : uri.replace(port: 8765);
}

class CommandClient {
  CommandClient(this.base);
  final Uri base;
  final _http = HttpClient()..connectionTimeout = const Duration(seconds: 10);

  static String newId() {
    final random = Random.secure();
    final b = List<int>.generate(16, (_) => random.nextInt(256));
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    final h = b.map((v) => v.toRadixString(16).padLeft(2, '0')).join();
    return '${h.substring(0, 8)}-${h.substring(8, 12)}-${h.substring(12, 16)}-${h.substring(16, 20)}-${h.substring(20)}';
  }

  Future<Map<String, dynamic>> request(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    final req = await _http
        .openUrl(method, base.resolve(path))
        .timeout(const Duration(seconds: 10));
    if (body != null) {
      req.headers.contentType = ContentType.json;
      req.write(jsonEncode(body));
    }
    final res = await req.close().timeout(const Duration(seconds: 15));
    final raw = await utf8.decoder
        .bind(res)
        .join()
        .timeout(const Duration(seconds: 15));
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw HttpException('PC 응답 오류 (${res.statusCode})');
    }
    final value = jsonDecode(raw);
    if (value is! Map<String, dynamic>) {
      throw const FormatException('잘못된 PC 응답');
    }
    return value;
  }

  void close() => _http.close(force: true);
}

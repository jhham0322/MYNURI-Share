// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mynuri/features/commands/command_client.dart';
import 'package:mynuri/features/commands/widgets/provider_settings_dialog.dart';

class FakeClient implements CommandClient {
  FakeClient(this.base, {this.fail = false});
  @override
  final Uri base;
  final bool fail;
  String provider = 'codebuddy-code';
  @override
  Future<Map<String, dynamic>> request(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    if (fail) throw Exception('연결 실패');
    if (method == 'POST') provider = body!['provider'] as String;
    return {'provider': provider};
  }

  @override
  void close() {}
}

void main() {
  testWidgets('PC에서 현재 처리기를 읽고 변경해 저장한다', (tester) async {
    final uri = Uri.parse('http://localhost:8765');
    final client = FakeClient(uri);
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => showDialog<void>(
              context: context,
              builder: (_) => ProviderSettingsDialog(
                address: uri,
                clientFactory: (_) => client,
              ),
            ),
            child: const Text('열기'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('열기'));
    await tester.pumpAndSettle();
    expect(find.text('CodeBuddy'), findsOneWidget);
    await tester.tap(find.text('Codex'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('저장'));
    await tester.pumpAndSettle();
    expect(client.provider, 'codex');
    expect(find.text('작업 처리기'), findsNothing);
  });

  testWidgets('연결 실패 시 재시도 안내를 표시한다', (tester) async {
    final uri = Uri.parse('http://localhost:8765');
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ProviderSettingsDialog(
            address: uri,
            clientFactory: (_) => FakeClient(uri, fail: true),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('다시 불러오기'), findsOneWidget);
    expect(find.textContaining('설정을 읽지 못했습니다'), findsOneWidget);
  });
}

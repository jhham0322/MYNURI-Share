// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mynuri/features/memo/memo_page.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('test/memo');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  tearDown(() => messenger.setMockMethodCallHandler(channel, null));

  testWidgets('저장된 메모를 보여주고 확인 후 삭제한다', (tester) async {
    final items = <Map<String, Object>>[
      {'id': 1, 'text': '우유 사기', 'createdAt': 1700000000000},
    ];
    messenger.setMockMethodCallHandler(channel, (call) async {
      if (call.method == 'listMemos') return items;
      if (call.method == 'deleteMemo') {
        items.removeWhere((item) => item['id'] == call.arguments['id']);
        return true;
      }
      throw MissingPluginException();
    });
    await tester.pumpWidget(
      const MaterialApp(home: MemoPage(channel: channel)),
    );
    await tester.pumpAndSettle();
    expect(find.text('우유 사기'), findsOneWidget);
    await tester.tap(find.byTooltip('메모 삭제'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('삭제'));
    await tester.pumpAndSettle();
    expect(find.text('아직 기록된 메모가 없습니다.'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('저장소 오류를 사용자에게 표시한다', (tester) async {
    messenger.setMockMethodCallHandler(
      channel,
      (_) async => throw PlatformException(code: 'failed'),
    );
    await tester.pumpWidget(
      const MaterialApp(home: MemoPage(channel: channel)),
    );
    await tester.pumpAndSettle();
    expect(find.text('메모를 불러오지 못했습니다.'), findsOneWidget);
    expect(find.text('다시 시도'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
}

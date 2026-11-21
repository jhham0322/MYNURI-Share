// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mynuri/features/commands/widgets/conversation_view.dart';

void main() {
  testWidgets('답변 본문을 누르면 해당 답변만 다시 읽는다', (tester) async {
    final controller = ScrollController();
    addTearDown(controller.dispose);
    final replayed = <int>[];
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationView(
            messages: const ['나: 질문', '누리: 같은 답변', '누리: 같은 답변'],
            controller: controller,
            onReplay: replayed.add,
          ),
        ),
      ),
    );
    await tester.tap(find.text('질문'));
    expect(replayed, isEmpty);
    await tester.tap(find.text('같은 답변').first);
    await tester.tap(find.text('같은 답변').last);
    expect(replayed, [2, 1]);
  });

  testWidgets('작은 화면에서도 가장 최근 대화부터 표시한다', (tester) async {
    final controller = ScrollController();
    addTearDown(controller.dispose);
    tester.view.physicalSize = const Size(360, 280);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final messages = List.generate(20, (i) => '나: 이전 메시지 $i');
    messages.add('누리: 가장 최근 응답');
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationView(messages: messages, controller: controller),
        ),
      ),
    );
    expect(find.text('가장 최근 응답'), findsOneWidget);
    expect(find.text('이전 메시지 0'), findsNothing);
    expect(
      tester.getBottomLeft(find.text('가장 최근 응답')).dy,
      lessThanOrEqualTo(280),
    );
  });

  testWidgets('새 대화가 추가되면 목록 아래에 표시한다', (tester) async {
    final controller = ScrollController();
    addTearDown(controller.dispose);
    final messages = List.generate(20, (i) => '나: 이전 메시지 $i');
    Widget view() => MaterialApp(
      home: Scaffold(
        body: ConversationView(messages: messages, controller: controller),
      ),
    );
    await tester.pumpWidget(view());
    messages.add('누리: 새 응답');
    await tester.pumpWidget(view());
    expect(find.text('새 응답'), findsOneWidget);
    expect(controller.position.pixels, 0);
  });
}

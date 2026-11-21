import 'package:flutter/widgets.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mynuri/features/commands/command_client.dart';
import 'package:mynuri/main.dart';

void main() {
  test('PC IP 주소에 기본 포트를 붙이고 잘못된 주소는 거부한다', () {
    expect(
      parsePcAddress('192.168.0.10').toString(),
      'http://192.168.0.10:8765',
    );
    expect(
      parsePcAddress('http://192.168.0.10:9000').toString(),
      'http://192.168.0.10:9000',
    );
    expect(parsePcAddress(''), isNull);
    expect(parsePcAddress('ftp://example.com'), isNull);
    expect(parsePcAddress('http://user:pass@example.com'), isNull);
  });
  test('요청 ID는 UUID 형식이며 중복되지 않는다', () {
    final first = CommandClient.newId();
    final second = CommandClient.newId();
    expect(
      first,
      matches(
        RegExp(
          r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
        ),
      ),
    );
    expect(first, isNot(second));
  });

  testWidgets('PC 주소는 취소하면 유지되고 저장할 때만 바뀐다', (tester) async {
    FlutterSecureStorage.setMockInitialValues({
      'address': 'http://192.168.0.10:8765',
    });
    await tester.pumpWidget(const NuriApp());
    await tester.pumpAndSettle();
    final settings = find.byTooltip('연결 설정');
    final field = find.byWidgetPredicate(
      (widget) =>
          widget is EditableText && widget.keyboardType == TextInputType.url,
    );

    await tester.tap(settings);
    await tester.pumpAndSettle();
    await tester.enterText(field, '192.168.0.20');
    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();

    await tester.tap(settings);
    await tester.pumpAndSettle();
    expect(
      tester.widget<EditableText>(field).controller.text,
      'http://192.168.0.10:8765',
    );
    await tester.enterText(field, '192.168.0.30');
    await tester.tap(find.text('저장'));
    await tester.pumpAndSettle();
    expect(
      await const FlutterSecureStorage().read(key: 'address'),
      'http://192.168.0.30:8765',
    );
    await tester.pumpWidget(const SizedBox.shrink());
  });
}

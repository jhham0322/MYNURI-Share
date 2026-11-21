// ignore: deprecated_member_use
import 'package:flutter/material.dart';

/// 최근 대화가 입력창에 가깝게 유지되는 대화 목록이다.
class ConversationView extends StatelessWidget {
  const ConversationView({
    super.key,
    required this.messages,
    required this.controller,
    this.assistantName = '누리',
    this.assistantAvatar,
    this.onReplay,
  });

  final List<String> messages;
  final ScrollController controller;
  final String assistantName;
  final String? assistantAvatar;
  final ValueChanged<int>? onReplay;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    if (messages.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.forum_outlined, size: 42, color: colors.primary),
              const SizedBox(height: 12),
              Text(
                '무엇을 도와드릴까요?',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 6),
              Text(
                '문자를 입력하거나 마이크로 말씀해 주세요.',
                textAlign: TextAlign.center,
                style: TextStyle(color: colors.onSurfaceVariant),
              ),
            ],
          ),
        ),
      );
    }
    return ListView.builder(
      key: const ValueKey('conversation'),
      controller: controller,
      reverse: true,
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
      itemCount: messages.length,
      itemBuilder: (context, index) {
        final messageIndex = messages.length - 1 - index;
        final message = messages[messageIndex];
        final mine = message.startsWith('나: ');
        final replay = !mine && onReplay != null
            ? () => onReplay!(messageIndex)
            : null;
        final text = mine
            ? message.substring(3)
            : message.startsWith('누리: ')
            ? message.substring(4)
            : message;
        return Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Align(
            alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
            child: ConstrainedBox(
              constraints: BoxConstraints(
                maxWidth: MediaQuery.sizeOf(context).width * .8,
              ),
              child: Column(
                crossAxisAlignment: mine
                    ? CrossAxisAlignment.end
                    : CrossAxisAlignment.start,
                children: [
                  Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 5,
                      vertical: 4,
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (!mine && assistantAvatar != null) ...[
                          CircleAvatar(
                            radius: 12,
                            foregroundImage: AssetImage(assistantAvatar!),
                          ),
                          const SizedBox(width: 6),
                        ],
                        Text(
                          mine ? '나' : assistantName,
                          style: Theme.of(context).textTheme.labelMedium
                              ?.copyWith(color: colors.onSurfaceVariant),
                        ),
                      ],
                    ),
                  ),
                  GestureDetector(
                    onTap: replay,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 15,
                        vertical: 11,
                      ),
                      decoration: BoxDecoration(
                        color: mine
                            ? colors.primary
                            : colors.surfaceContainerHighest,
                        borderRadius: BorderRadius.circular(18),
                      ),
                      child: SelectableText(
                        text,
                        onTap: replay,
                        style: TextStyle(
                          color: mine ? colors.onPrimary : colors.onSurface,
                          height: 1.35,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

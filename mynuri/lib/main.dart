import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
// ignore: deprecated_member_use
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:path_provider/path_provider.dart';
import 'package:speech_to_text/speech_to_text.dart';

import 'features/commands/command_client.dart';
import 'features/commands/widgets/conversation_view.dart';
import 'features/commands/widgets/custom_voice_dialog.dart';
import 'features/commands/widgets/live_mode_control.dart';
import 'features/commands/widgets/provider_settings_dialog.dart';
import 'features/memo/memo_page.dart';
import 'features/update/update_config.dart';
import 'features/update/update_dialog.dart';
import 'features/update/update_service.dart';

const isAssistant = String.fromEnvironment('NURI_APP_ROLE') == 'assistant';
const appName = isAssistant ? '도우미' : '누리';
const isNuriWorker = !isAssistant && bool.fromEnvironment('NURI_WORKER');
const managedUpdateRole = isAssistant ? 'assistant' : (isNuriWorker ? 'nuri' : '');
const assistantIcon = 'assets/header_icon.png';

void main() => runApp(const NuriApp());

class NuriApp extends StatelessWidget {
  const NuriApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: appName,
    theme: ThemeData(
      colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xff3467a9)),
      useMaterial3: true,
    ),
    home: const CommandPage(),
  );
}

class CommandPage extends StatefulWidget {
  const CommandPage({super.key});
  @override
  State<CommandPage> createState() => _CommandPageState();
}

class _CommandPageState extends State<CommandPage> with WidgetsBindingObserver {
  static const voiceChannel = MethodChannel('mynuri/voice_wait');
  static const updateChannel = MethodChannel('mynuri/updater');
  String voiceStatus = '꺼짐';
  String mobileMode = 'standby';
  bool voiceOutputEnabled = false;
  bool checkingUpdate = false;
  File? pendingUpdateInstall;
  Timer? voiceTimer;
  Timer? workerTimer;
  Timer? updateTimer;
  Timer? pendingRefreshTimer;
  int? attemptedUpdateBuild;
  String? lastWorkerStatus;
  String _pcAddress = '';
  final input = TextEditingController();
  final conversationScroll = ScrollController();
  final inputFocus = FocusNode();
  final speech = SpeechToText();
  final storage = const FlutterSecureStorage();
  final messages = <String>[];
  final answers = <int, ({String id, String prompt, String text})>{};
  bool busy = false;
  int _activeAnswerSpeech = 0;
  bool get _hasActivity =>
      busy || pendingId != null || _activeAnswerSpeech > 0 || input.text.isNotEmpty;
  bool voiceInput = false;
  String status = 'PC 연결을 설정해 주세요';
  String? pendingId;
  String? pendingText;
  String? pendingType;
  String? visiblePromptId;
  bool autoResumePending = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    inputFocus.addListener(_onInputFocusChanged);
    _load();
    _refreshVoice();
    WidgetsBinding.instance.addPostFrameCallback((_) => _checkUpdate());
    if (managedUpdateRole.isNotEmpty) {
      updateTimer = Timer.periodic(const Duration(minutes: 30), (_) => _checkUpdate());
    }
    voiceTimer = Timer.periodic(
      const Duration(seconds: 3),
      (_) => _refreshVoice(),
    );
    if (isNuriWorker) {
      workerTimer = Timer.periodic(
        const Duration(seconds: 3),
        (_) => _pollWorkerStatus(),
      );
    }
    if (isAssistant) {
      pendingRefreshTimer = Timer.periodic(const Duration(seconds: 15), (_) {
        if (!busy && autoResumePending && pendingId != null &&
            WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed) {
          unawaited(_send(resume: true));
        }
      });
    }
  }

  Future<void> _startWorker() async {
    if (!isNuriWorker || !mounted) return;
    final address = parsePcAddress(_pcAddress);
    if (address == null) return;
    try {
      await voiceChannel.invokeMethod<void>('startCommandWorker', {
        'address': address.toString(),
      });
    } on PlatformException catch (error) {
      if (mounted) setState(() => status = error.message ?? '누리 명령 수신 오류');
    }
  }

  Future<void> _pollWorkerStatus() async {
    final message = await voiceChannel.invokeMethod<String>('commandWorkerStatus');
    if (mounted && !busy && message != null && message != lastWorkerStatus) {
      lastWorkerStatus = message;
      setState(() => status = message);
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _refreshVoice();
      _startWorker();
      final file = pendingUpdateInstall;
      if (file != null) {
        pendingUpdateInstall = null;
        _resumeUpdateInstall(file);
      } else if (managedUpdateRole.isNotEmpty) {
        _checkUpdate();
      }
    }
  }

  void _onInputFocusChanged() {
    if (inputFocus.hasFocus) _showLatestMessage();
  }

  void _showLatestMessage() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !conversationScroll.hasClients) return;
      conversationScroll.animateTo(
        0,
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOut,
      );
    });
  }

  Future<void> _refreshVoice() async {
    try {
      final current = await voiceChannel.invokeMethod<String>('status');
      final mode = await voiceChannel.invokeMethod<String>('mode');
      final outputEnabled = await voiceChannel.invokeMethod<bool>(
        'outputStatus',
      );
      final command = input.text.trim().isEmpty
          ? await voiceChannel.invokeMethod<String>('peekCommand')
          : null;
      if (!mounted) return;
      setState(() {
        voiceStatus = current ?? '꺼짐';
        mobileMode = mode == 'memo' ? 'memo' : 'standby';
        voiceOutputEnabled = outputEnabled ?? false;
        if (command != null &&
            command.isNotEmpty &&
            input.text.trim().isEmpty) {
          input.text = command;
          voiceInput = true;
          status = '인식한 지시를 확인하고 전송하세요';
        }
      });
      if (command != null && input.text == command) {
        await voiceChannel.invokeMethod<void>('clearCommand', {
          'text': command,
        });
      }
    } on MissingPluginException {
      // Android 외 플랫폼은 지원하지 않는다.
      if (mounted) setState(() => voiceStatus = 'Android에서 사용 가능');
    } catch (_) {
      if (mounted) setState(() => voiceStatus = '상태를 확인하지 못했습니다');
    }
  }

  Future<void> _toggleVoice() async {
    try {
      final active =
          voiceStatus == '음성 인식 준비 중' ||
          voiceStatus == '누리야 호출 대기 중' ||
          voiceStatus == '지시를 듣는 중' ||
          voiceStatus == '메모 기록 중' ||
          voiceStatus == '마이크 사용 대기 중 · 자동 재시도';
      await voiceChannel.invokeMethod<void>(active ? 'stop' : 'start');
      await _refreshVoice();
    } catch (_) {
      if (mounted) setState(() => voiceStatus = '음성 대기를 시작하지 못했습니다');
    }
  }

  Future<void> _changeMobileMode() async {
    if (defaultTargetPlatform != TargetPlatform.android) {
      setState(() => status = '모바일 메모 모드는 Android에서 지원합니다');
      return;
    }
    final chosen = await showDialog<String>(
      context: context,
      builder: (context) => SimpleDialog(
        title: const Text('모바일 동작 모드'),
        children: [
          SimpleDialogOption(
            onPressed: () => Navigator.pop(context, 'standby'),
            child: const ListTile(
              title: Text('대기 모드'),
              subtitle: Text('누리야 호출을 기다립니다'),
            ),
          ),
          SimpleDialogOption(
            onPressed: () => Navigator.pop(context, 'memo'),
            child: const ListTile(
              title: Text('메모 모드'),
              subtitle: Text('호출어 없이 말한 내용을 글로 기록합니다'),
            ),
          ),
        ],
      ),
    );
    if (chosen == null || !mounted) return;
    try {
      if (speech.isListening) await speech.stop();
      await voiceChannel.invokeMethod<void>('setMode', {'mode': chosen});
      if (chosen == 'memo') {
        final active = await voiceChannel.invokeMethod<bool>('active') ?? false;
        if (!active) await voiceChannel.invokeMethod<void>('start');
      }
      await _refreshVoice();
      if (mounted) {
        setState(
          () => status = chosen == 'memo' ? '메모 모드를 선택했습니다' : '대기 모드를 선택했습니다',
        );
      }
    } catch (_) {
      await _refreshVoice();
      if (mounted) setState(() => status = '모드 설정 또는 음성 인식을 시작하지 못했습니다');
    }
  }

  Future<void> _openMemos() async {
    if (defaultTargetPlatform != TargetPlatform.android) {
      setState(() => status = '모바일 메모는 Android에서 지원합니다');
      return;
    }
    await Navigator.of(context)
        .push(MaterialPageRoute<void>(builder: (_) => const MemoPage()));
  }

  Future<void> _toggleOutput() async {
    try {
      await voiceChannel.invokeMethod<void>('setOutput', {
        'enabled': !voiceOutputEnabled,
      });
      await _refreshVoice();
    } catch (_) {
      if (mounted) setState(() => status = '음성 출력 설정을 변경하지 못했습니다');
    }
  }

  Future<void> _load() async {
    try {
      final saved = await storage.readAll();
      if (!mounted) return;
      setState(() {
        _pcAddress = saved['address'] ?? '';
        pendingId = saved['pendingId'];
        pendingText = saved['pendingText'];
        pendingType = saved['pendingType'];
        if (pendingId != null) status = '이전 요청 결과를 확인해 주세요';
      });
      if (isAssistant) {
        final history = jsonDecode(saved['recentAnswers'] ?? '[]') as List;
        if (messages.isEmpty) {
          setState(() {
            for (final entry in history.take(3)) {
              if (entry case {'id': final String id, 'prompt': final String prompt, 'text': final String text}) {
                _addAssistantAnswer(id, prompt, text, includePrompt: true);
              }
            }
          });
        }
        await _retainRecentVoice();
      }
      await _startWorker();
    } catch (_) {
      if (mounted) setState(() => status = '저장된 연결 설정을 읽지 못했습니다');
    }
  }

  void _addAssistantAnswer(String id, String prompt, String text, {required bool includePrompt}) {
    if (answers.values.any((answer) => answer.id == id)) return;
    if (includePrompt) messages.add('나: $prompt');
    answers[messages.length] = (id: id, prompt: prompt, text: text);
    messages.add('누리: $text');
  }

  Future<void> _retainRecentVoice({bool save = false}) async {
    final recent = answers.values.toList().reversed.take(3).toList().reversed;
    if (save) {
      await storage.write(
        key: 'recentAnswers',
        value: jsonEncode([
          for (final answer in recent)
            {'id': answer.id, 'prompt': answer.prompt, 'text': answer.text},
        ]),
      );
    }
    try {
      await voiceChannel.invokeMethod<void>('retainAssistantVoice', {
        'ids': [for (final answer in recent) answer.id],
      });
    } on MissingPluginException {
      // Android stores synthesized audio.
    }
  }

  Future<void> _speakAnswer(String id, String text, {bool replay = false}) async {
    if (!mounted) return;
    setState(() => _activeAnswerSpeech++);
    try {
      await voiceChannel.invokeMethod<void>('speak', {
        'text': text,
        'assistant': isAssistant,
        if (isAssistant) 'answerId': id,
        if (replay) 'replay': true,
      });
    } on PlatformException catch (e) {
      if (mounted && (!isAssistant || !busy)) {
        setState(() => status = e.message ?? '음성을 재생하지 못했습니다');
      }
    } on MissingPluginException {
      // Android provides response playback.
    } finally {
      if (mounted) setState(() => _activeAnswerSpeech--);
    }
  }

  Future<void> _checkUpdate({bool manual = false}) async {
    if (checkingUpdate) return;
    final managed = managedUpdateRole.isNotEmpty;
    if (!manual && managed && (_hasActivity ||
        WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed)) return;
    if (defaultTargetPlatform != TargetPlatform.android) {
      if (manual && mounted) {
        setState(() => status = '업데이트는 Android에서만 지원합니다');
      }
      return;
    }
    final uri = updateManifestUri();
    if (uri == null) {
      if (manual && mounted) {
        setState(() => status = '업데이트 주소가 설정되지 않았습니다');
      }
      return;
    }
    checkingUpdate = true;
    if (manual && mounted) setState(() => status = '업데이트 확인 중');
    final service = UpdateService();
    try {
      final manifest = await service.fetchManifest(uri);
      if (manifest == null) {
        throw const FormatException('버전 정보 형식이 올바르지 않습니다');
      }
      if (managed && (manifest.channel != managedUpdateRole ||
          manifest.fileName != '$managedUpdateRole-${manifest.version}.apk')) {
        throw const FormatException('이 기기용 업데이트가 아닙니다');
      }
      final info = await PackageInfo.fromPlatform();
      final currentBuild = int.tryParse(info.buildNumber) ?? 0;
      if (!manifest.isNewerThan(
        currentVersion: info.version,
        currentBuild: currentBuild,
      )) {
        if (manual && mounted) {
          setState(() => status = '최신 버전입니다 (${info.version})');
        }
        return;
      }
      if (!mounted) return;
      if (managed) {
        if ((!manual && attemptedUpdateBuild == manifest.build) ||
            _hasActivity ||
            WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed) return;
        final updates = Directory('${(await getTemporaryDirectory()).path}/updates');
        final file = await service.download(
          manifest, directory: updates, source: uri.resolve(manifest.fileName),
        );
        if (!mounted || _hasActivity ||
            WidgetsBinding.instance.lifecycleState != AppLifecycleState.resumed) return;
        attemptedUpdateBuild = manifest.build;
        await _installUpdate(file);
        return;
      }
      if (!await confirmUpdate(context, manifest)) return;
      if (!mounted) return;
      final directory = Directory(
        '${(await getTemporaryDirectory()).path}/updates',
      );
      if (!mounted) return;
      final file = await showDialog<File>(
        context: context,
        barrierDismissible: false,
        builder: (context) => UpdateProgressDialog(
          download: (onProgress) => service.download(
            manifest,
            directory: directory,
            source: debugUpdateDownloadUri(),
            onProgress: onProgress,
          ),
        ),
      );
      if (file == null) return;
      await _installUpdate(file);
    } catch (_) {
      if (mounted && manual) {
        setState(() => status = '업데이트를 확인하지 못했습니다');
      }
    } finally {
      service.close();
      checkingUpdate = false;
    }
  }

  Future<void> _installUpdate(File file) async {
    try {
      final canInstall =
          await updateChannel.invokeMethod<bool>('canInstall') ?? false;
      if (!canInstall) {
        if (!mounted) return;
        if (await confirmInstallPermission(context)) {
          pendingUpdateInstall = file;
          try {
            await updateChannel.invokeMethod<void>('openInstallSettings');
          } catch (_) {
            pendingUpdateInstall = null;
            rethrow;
          }
        }
        return;
      }
      await updateChannel.invokeMethod<void>(
        managedUpdateRole.isEmpty ? 'installApk' : 'installManagedUpdate',
        {'path': file.path},
      );
    } catch (_) {
      if (mounted) setState(() => status = '업데이트 설치 화면을 열지 못했습니다');
    }
  }

  Future<void> _resumeUpdateInstall(File file) async {
    try {
      final canInstall =
          await updateChannel.invokeMethod<bool>('canInstall') ?? false;
      if (!mounted) return;
      if (canInstall && await file.exists()) {
        await updateChannel.invokeMethod<void>(
          managedUpdateRole.isEmpty ? 'installApk' : 'installManagedUpdate', {
          'path': file.path,
        });
      } else {
        setState(() => status = '설치 권한 또는 업데이트 파일을 확인해 주세요');
      }
    } catch (_) {
      if (mounted) setState(() => status = '업데이트 설치 화면을 열지 못했습니다');
    }
  }

  Future<void> _providerSettings() async {
    final uri = parsePcAddress(_pcAddress);
    if (uri == null) {
      setState(() => status = 'PC IP 주소를 먼저 설정해 주세요');
      return;
    }
    final changed = await showDialog<bool>(
      context: context,
      builder: (_) => ProviderSettingsDialog(address: uri),
    );
    if (changed == true && mounted) {
      setState(() => status = '작업 처리기 설정을 저장했습니다');
    }
  }

  Future<void> _settings() async {
    var draft = _pcAddress;
    final selected = await showDialog<Uri>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('PC 누리앱 연결'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextFormField(
                initialValue: draft,
                onChanged: (value) => draft = value,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  labelText: 'PC IP 주소',
                  hintText: '예: 192.168.0.10',
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('취소'),
          ),
          FilledButton(
            onPressed: () {
              final uri = parsePcAddress(draft);
              if (uri == null) {
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('올바른 PC IP 주소를 입력해 주세요.')),
                  );
                }
                return;
              }
              Navigator.pop(context, uri);
            },
            child: const Text('저장'),
          ),
        ],
      ),
    );
    if (selected == null || !mounted) return;
    try {
      await storage.write(key: 'address', value: selected.toString());
      if (mounted) {
        setState(() {
          _pcAddress = selected.toString();
          status = 'PC 연결 주소를 저장했습니다';
        });
        await _startWorker();
      }
    } catch (_) {
      if (mounted) setState(() => status = 'PC 연결 주소를 저장하지 못했습니다');
    }
  }

  Future<void> _listen() async {
    if (voiceStatus == '음성 인식 준비 중' ||
        voiceStatus == '누리야 호출 대기 중' ||
        voiceStatus == '지시를 듣는 중' ||
        voiceStatus == '메모 기록 중' ||
        voiceStatus == '마이크 사용 대기 중 · 자동 재시도') {
      setState(() => status = '음성 대기를 끈 뒤 수동 음성 입력을 사용해 주세요');
      return;
    }
    try {
      if (speech.isListening) {
        await speech.stop();
        if (mounted) setState(() {});
        return;
      }
      final ready = await speech.initialize(
        onError: (error) {
          if (mounted) setState(() => status = '음성 인식 오류: ${error.errorMsg}');
        },
        onStatus: (_) {
          if (mounted) setState(() {});
        },
      );
      if (!ready) {
        if (mounted) setState(() => status = '음성 인식을 사용할 수 없습니다');
        return;
      }
      await speech.listen(
        listenOptions: SpeechListenOptions(localeId: 'ko_KR'),
        onResult: (result) {
          if (!mounted) return;
          setState(() {
            input.text = result.recognizedWords;
            input.selection = TextSelection.collapsed(
              offset: input.text.length,
            );
            voiceInput = true;
          });
        },
      );
      if (mounted) setState(() => status = '말씀해 주세요. 인식 후 내용을 확인하고 전송하세요.');
    } catch (_) {
      if (mounted) setState(() => status = '마이크 권한 또는 음성 인식 설정을 확인해 주세요');
    }
  }

  Future<void> _send({bool resume = false}) async {
    if (busy) return;
    final text = resume ? pendingText : input.text.trim();
    final id = resume ? pendingId : CommandClient.newId();
    final type = resume ? pendingType : (voiceInput ? 'voice' : 'text');
    final uri = parsePcAddress(_pcAddress);
    if (text == null || text.isEmpty || id == null) return;
    if (uri == null) {
      setState(() => status = 'PC IP 주소를 먼저 설정해 주세요');
      return;
    }
    setState(() {
      busy = true;
      if (!resume) autoResumePending = true;
      status = resume ? '요청 상태 확인 중' : '전송 중';
    });
    final client = CommandClient(uri);
    try {
      if (!resume) {
        await storage.write(key: 'pendingId', value: id);
        await storage.write(key: 'pendingText', value: text);
        await storage.write(key: 'pendingType', value: type);
        pendingId = id;
        pendingText = text;
        pendingType = type;
        if (mounted) {
          setState(() {
            messages.add('나: $text');
            visiblePromptId = id;
            input.clear();
            voiceInput = false;
          });
          _showLatestMessage();
        }
      }
      // A resumed request uses the same durable ID, even if the first
      // connection disconnected before the relay acknowledged it.
      await client.request('POST', '/v1/commands', {
        'request_id': id,
        'text': text,
        'input_type': type,
      });
      for (var i = 0; i < 90; i++) {
        if (!mounted) return;
        final reply = await client.request('GET', '/v1/commands/$id');
        final state = reply['status'];
        if (state == 'completed') {
          final result = reply['result'];
          final response = result is Map ? result['text']?.toString() : null;
          if (response == null) throw const FormatException('결과가 없습니다');
          if (mounted) {
            setState(() {
              if (isAssistant) {
                _addAssistantAnswer(id, text, response, includePrompt: visiblePromptId != id);
              } else {
                messages.add('누리: $response');
              }
              status = '완료';
            });
            _showLatestMessage();
          }
          if (isAssistant && mounted) await _retainRecentVoice(save: true);
          await _clearPending();
          if (isAssistant) {
            if (mounted) unawaited(_speakAnswer(id, response));
          } else {
            await _speakAnswer(id, response);
          }
          return;
        }
        if (state == 'failed') {
          if (mounted) setState(() => status = 'PC에서 지시를 처리하지 못했습니다');
          await _clearPending();
          return;
        }
        if (state == 'unknown') {
          autoResumePending = false;
          if (mounted) {
            setState(
              () => status = 'PC 작업 수행 여부를 확인할 수 없습니다. PC에서 결과를 확인해 주세요.',
            );
          }
          return;
        }
        if (state != 'queued' && state != 'running') {
          throw const FormatException('알 수 없는 상태');
        }
        if (mounted) {
          setState(
            () => status = state == 'queued' ? 'PC에서 대기 중' : 'PC에서 수행 중',
          );
        }
        await Future<void>.delayed(const Duration(seconds: 2));
      }
      if (mounted) {
        setState(() => status = '응답 대기 시간이 지났습니다. 요청 상태를 다시 확인해 주세요.');
      }
    } catch (_) {
      if (mounted) {
        setState(() => status = '연결 또는 응답을 확인하지 못했습니다. 같은 요청의 상태를 확인해 주세요.');
      }
    } finally {
      client.close();
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> _clearPending() async {
    for (final key in ['pendingId', 'pendingText', 'pendingType']) {
      await storage.delete(key: key);
    }
    pendingId = null;
    pendingText = null;
    pendingType = null;
  }

  Future<void> _forgetPending() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('요청 확인 중단'),
        content: const Text(
          'PC에서 이미 작업을 수행했을 수 있습니다. PC에서 결과를 확인한 뒤 요청 확인을 중단하세요.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('취소'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('확인 중단'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    await _clearPending();
    if (mounted) setState(() => status = '요청 확인을 중단했습니다');
  }

  @override
  void dispose() {
    pendingRefreshTimer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    voiceTimer?.cancel();
    workerTimer?.cancel();
    updateTimer?.cancel();
    speech.stop();
    inputFocus.removeListener(_onInputFocusChanged);
    inputFocus.dispose();
    conversationScroll.dispose();
    input.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final voiceActive =
        voiceStatus == '음성 인식 준비 중' ||
        voiceStatus == '누리야 호출 대기 중' ||
        voiceStatus == '지시를 듣는 중' ||
        voiceStatus == '메모 기록 중' ||
        voiceStatus == '마이크 사용 대기 중 · 자동 재시도';
    return Scaffold(
      backgroundColor: colors.surface,
      appBar: AppBar(
        leading: Padding(
          padding: const EdgeInsets.all(9),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: Image.asset(
              isAssistant ? assistantIcon : 'assets/header_icon.png',
              fit: BoxFit.cover,
              semanticLabel: '$appName 앱 아이콘',
            ),
          ),
        ),
        title: const Text(appName, overflow: TextOverflow.ellipsis),
        actions: [
          IconButton(
            onPressed: () => _checkUpdate(manual: true),
            icon: const Icon(Icons.system_update_alt_outlined),
            tooltip: '업데이트 확인',
          ),
          IconButton(
            onPressed: _providerSettings,
            icon: const Icon(Icons.smart_toy_outlined),
            tooltip: '작업 처리기 설정',
          ),
          IconButton(
            onPressed: busy || pendingId != null ? null : _settings,
            icon: const Icon(Icons.settings_outlined),
            tooltip: '연결 설정',
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            if (managedUpdateRole.isNotEmpty)
              LiveModeControl(address: parsePcAddress(_pcAddress), role: managedUpdateRole),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 2, 16, 6),
              child: Row(
                children: [
                  Icon(
                    busy ? Icons.hourglass_top_rounded : Icons.circle,
                    size: busy ? 16 : 9,
                    color: colors.primary,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      status,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
                ],
              ),
            ),
            if (pendingId != null)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: Wrap(
                  spacing: 4,
                  children: [
                    TextButton(
                      onPressed: busy ? null : () => _send(resume: true),
                      child: const Text('이전 요청 상태 확인'),
                    ),
                    TextButton(
                      onPressed: busy ? null : _forgetPending,
                      child: const Text('요청 확인 중단'),
                    ),
                  ],
                ),
              ),
            SizedBox(
              height: 48,
              child: ListView(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                scrollDirection: Axis.horizontal,
                children: [
                  ActionChip(
                    avatar: const Icon(Icons.tune, size: 18),
                    label: Text(mobileMode == 'memo' ? '모드: 메모' : '모드: 대기'),
                    onPressed: _changeMobileMode,
                  ),
                  const SizedBox(width: 8),
                  ActionChip(
                    avatar: const Icon(Icons.sticky_note_2_outlined, size: 18),
                    label: const Text('메모 보기'),
                    onPressed: _openMemos,
                  ),
                  const SizedBox(width: 8),
                  ActionChip(
                    avatar: Icon(
                      voiceActive ? Icons.mic : Icons.mic_none,
                      size: 18,
                    ),
                    label: Text('음성 대기 ${voiceActive ? '켜짐' : '꺼짐'}'),
                    tooltip: voiceStatus,
                    onPressed: _toggleVoice,
                  ),
                  const SizedBox(width: 8),
                  ActionChip(
                    avatar: Icon(
                      voiceOutputEnabled
                          ? Icons.volume_up_outlined
                          : Icons.volume_off_outlined,
                      size: 18,
                    ),
                    label: Text('음성 출력 ${voiceOutputEnabled ? '켜짐' : '꺼짐'}'),
                    onPressed: _toggleOutput,
                  ),
                  if (defaultTargetPlatform == TargetPlatform.android) ...[
                    const SizedBox(width: 8),
                    ActionChip(
                      avatar: const Icon(Icons.record_voice_over_outlined, size: 18),
                      label: const Text('맞춤 목소리'),
                      onPressed: () => showDialog<void>(
                        context: context,
                        barrierDismissible: false,
                        builder: (_) => const CustomVoiceDialog(),
                      ),
                    ),
                  ],
                ],
              ),
            ),
            if (!voiceActive &&
                voiceStatus != '꺼짐' &&
                voiceStatus != 'Android에서 사용 가능')
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: Text(
                    voiceStatus,
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
              ),
            const Divider(height: 1),
            Expanded(
              child: ConversationView(
                messages: messages,
                controller: conversationScroll,
                assistantName: appName,
                assistantAvatar: isAssistant ? assistantIcon : null,
                onReplay: isAssistant ? (index) {
                  final answer = answers[index];
                  if (answer != null) unawaited(_speakAnswer(answer.id, answer.text, replay: true));
                } : null,
              ),
            ),
            Container(
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 10),
              decoration: BoxDecoration(
                color: colors.surface,
                border: Border(top: BorderSide(color: colors.outlineVariant)),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: TextField(
                      controller: input,
                      focusNode: inputFocus,
                      minLines: 1,
                      maxLines: 4,
                      onChanged: (_) => voiceInput = false,
                      decoration: InputDecoration(
                        hintText: '지시를 입력하세요',
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 16,
                          vertical: 12,
                        ),
                        filled: true,
                        fillColor: colors.surfaceContainerHighest,
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(24),
                          borderSide: BorderSide.none,
                        ),
                      ),
                    ),
                  ),
                  IconButton(
                    onPressed: busy || _activeAnswerSpeech > 0 ? null : _listen,
                    icon: Icon(speech.isListening ? Icons.mic : Icons.mic_none),
                    tooltip: '음성 입력',
                  ),
                  IconButton.filled(
                    onPressed: busy || pendingId != null ? null : _send,
                    icon: const Icon(Icons.arrow_upward),
                    tooltip: '전송',
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

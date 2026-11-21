package com.example.mynuri

import android.app.ActivityManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer

class VoiceWaitService : Service(), RecognitionListener {
    private val handler = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var waitingForCommand = false
    private var listeningMode = "standby"
    private var stopped = false
    private var retryDelayMs = 2000L
    private val retry = Runnable { recreateAndListen() }
    private val watchdog = Runnable { suspendAndRetry() }

    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("nuri_voice", "누리 음성 대기", NotificationManager.IMPORTANCE_LOW))
        val notice = notification("기기 내 음성 인식을 준비하고 있습니다")
        if (Build.VERSION.SDK_INT >= 29) startForeground(42, notice, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        else startForeground(42, notice)
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || !SpeechRecognizer.isOnDeviceRecognitionAvailable(this)) {
            setState(this, false, "기기 내 음성 인식을 사용할 수 없습니다")
            stopSelf()
            return
        }
        try {
            setState(this, true, "음성 인식 준비 중")
            recreateAndListen()
        } catch (_: Exception) { fail("기기 내 음성 인식을 시작하지 못했습니다") }
    }

    private fun notification(text: String): Notification {
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pending = PendingIntent.getActivity(this, 1, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, "nuri_voice")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now).setContentTitle("모바일 누리 음성 인식")
            .setContentText(text).setOngoing(true).setContentIntent(pending).build()
    }

    private fun recreateAndListen() {
        if (stopped) return
        try {
            recognizer?.destroy()
            recognizer = SpeechRecognizer.createOnDeviceSpeechRecognizer(this).also { it.setRecognitionListener(this) }
            listen()
        } catch (_: Exception) { suspendAndRetry() }
    }

    private fun suspendAndRetry() {
        if (stopped) return
        handler.removeCallbacks(watchdog)
        handler.removeCallbacks(retry)
        setState(this, true, "마이크 사용 대기 중 · 자동 재시도")
        getSystemService(NotificationManager::class.java).notify(42, notification("마이크 사용 대기 중 · 자동 재시도"))
        handler.postDelayed(retry, retryDelayMs)
        retryDelayMs = (retryDelayMs * 2).coerceAtMost(30000L)
    }

    private fun koreanRecognitionIntent() = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
        putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ko-KR")
        putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
    }

    private fun listen() {
        if (stopped) return
        try {
            listeningMode = getSharedPreferences("nuri_voice", MODE_PRIVATE).getString("mode", "standby") ?: "standby"
            if (listeningMode == "memo") waitingForCommand = false
            recognizer?.startListening(koreanRecognitionIntent())
            handler.removeCallbacks(watchdog)
            handler.postDelayed(watchdog, 30000)
        } catch (_: Exception) { suspendAndRetry() }
    }

    private fun fail(message: String) { setState(this, false, message); stopped = true; stopSelf() }
    override fun onReadyForSpeech(params: Bundle?) {
        if (stopped) return
        val status = if (listeningMode == "memo") "메모 기록 중" else if (waitingForCommand) "지시를 듣는 중" else "누리야 호출 대기 중"
        setState(this, true, status)
        val hasCommand = getSharedPreferences("nuri_voice", MODE_PRIVATE).contains("command")
        getSystemService(NotificationManager::class.java).notify(42, notification(if (hasCommand && listeningMode != "memo") "지시를 인식했습니다. 눌러서 확인하세요." else status))
    }
    override fun onBeginningOfSpeech() {}
    override fun onRmsChanged(rmsdB: Float) {}
    override fun onBufferReceived(buffer: ByteArray?) {}
    override fun onEndOfSpeech() {}
    override fun onPartialResults(partialResults: Bundle?) {}
    override fun onEvent(eventType: Int, params: Bundle?) {}
    override fun onError(error: Int) {
        if (stopped) return
        handler.removeCallbacks(watchdog)
        handler.removeCallbacks(retry)
        if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) {
            waitingForCommand = false
            retryDelayMs = 2000L
            handler.postDelayed(retry, 500)
            return
        }
        when (error) {
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> {
                fail("모바일 누리의 마이크 권한을 허용해 주세요")
                return
            }
            SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED -> {
                fail("이 기기의 오프라인 음성 인식은 한국어를 지원하지 않습니다")
                return
            }
            SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE -> {
                prepareKoreanModel()
                return
            }
        }
        suspendAndRetry()
    }

    private fun prepareKoreanModel() {
        if (Build.VERSION.SDK_INT >= 33) {
            val engine = recognizer
            if (engine != null) {
                try {
                    engine.triggerModelDownload(koreanRecognitionIntent())
                    fail("한국어 오프라인 음성 모델 설치를 요청했습니다. 설치 후 음성 대기를 다시 켜 주세요")
                    return
                } catch (_: RuntimeException) {
                    // Some recognizers require installation through device settings.
                }
            }
        }
        fail("기기 설정에서 한국어 오프라인 음성 인식 언어를 설치해 주세요")
    }
    override fun onResults(results: Bundle?) {
        if (stopped) return
        handler.removeCallbacks(watchdog)
        handler.removeCallbacks(retry)
        retryDelayMs = 2000L
        val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty().trim()
        val currentMode = getSharedPreferences("nuri_voice", MODE_PRIVATE).getString("mode", "standby")
        // 모드가 바뀐 동안 시작된 인식 결과는 새 모드의 데이터로 잘못 기록하지 않는다.
        if (currentMode != listeningMode) {
            waitingForCommand = false
            handler.postDelayed(retry, 500)
            return
        }
        if (listeningMode == "memo") {
            if (text.isNotBlank()) {
                try {
                    MemoStore(this).use { it.add(text) }
                    getSystemService(NotificationManager::class.java).notify(42, notification("메모를 기록하고 있습니다"))
                } catch (_: Exception) {
                    fail("메모를 저장하지 못했습니다")
                    return
                }
            }
            handler.postDelayed(retry, 500)
            return
        }
        val index = if (text.startsWith("누리야")) 0 else -1
        val command = if (index >= 0) text.substring(index + 3).trim(' ', ',', '.', '!', '?') else if (waitingForCommand) text else ""
        waitingForCommand = index >= 0 && command.isEmpty()
        if (command.isNotBlank()) {
            waitingForCommand = false
            val prefs = getSharedPreferences("nuri_voice", MODE_PRIVATE)
            if (!prefs.contains("command")) prefs.edit().putString("command", command).apply()
            getSystemService(NotificationManager::class.java).notify(42, notification("지시를 인식했습니다. 눌러서 확인하세요."))
        }
        handler.postDelayed(retry, 500)
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_MODE_CHANGED && !stopped) {
            handler.removeCallbacks(retry)
            handler.removeCallbacks(watchdog)
            waitingForCommand = false
            recreateAndListen()
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        stopped = true
        handler.removeCallbacksAndMessages(null)
        recognizer?.destroy()
        val prefs = getSharedPreferences("nuri_voice", MODE_PRIVATE)
        if (prefs.getBoolean("active", false)) setState(this, false, "꺼짐")
        super.onDestroy()
    }
    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        const val ACTION_MODE_CHANGED = "com.example.mynuri.MODE_CHANGED"
        @Suppress("DEPRECATION")
        fun isRunning(context: Context): Boolean {
            val manager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
            return manager.getRunningServices(Int.MAX_VALUE).any { it.service.className == VoiceWaitService::class.java.name }
        }

        fun reconcile(context: Context) {
            val active = context.getSharedPreferences("nuri_voice", Context.MODE_PRIVATE).getBoolean("active", false)
            if (active && !isRunning(context)) setState(context, false, "서비스가 중지되었습니다")
        }

        fun setState(context: Context, active: Boolean, status: String) {
            context.getSharedPreferences("nuri_voice", MODE_PRIVATE).edit().putBoolean("active", active).putString("status", status).apply()
            VoiceWidget.refresh(context)
        }
    }
}

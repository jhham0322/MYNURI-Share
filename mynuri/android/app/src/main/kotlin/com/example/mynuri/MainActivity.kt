package com.example.mynuri

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.PackageInstaller
import android.app.PendingIntent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import com.example.mynuri.moss.CustomVoice
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File

class MainActivity : FlutterActivity() {
    companion object {
        @Volatile var updateActivityVisible = false
            private set
    }

    override fun onResume() {
        super.onResume()
        updateActivityVisible = true
        UpdateInstallResult.showConfirmation(this)
    }

    override fun onPause() {
        updateActivityVisible = false
        super.onPause()
    }

    private var waitingResult: MethodChannel.Result? = null
    private var voiceImportResult: MethodChannel.Result? = null
    private val channel = "mynuri/voice_wait"

    override fun configureFlutterEngine(engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        MethodChannel(engine.dartExecutor.binaryMessenger, channel).setMethodCallHandler { call, result ->
            when (call.method) {
                "startCommandWorker" -> {
                    val intent = Intent(this, CommandWorkerService::class.java)
                        .putExtra("address", call.argument<String>("address"))
                    try {
                        androidx.core.content.ContextCompat.startForegroundService(this, intent)
                        result.success(null)
                    } catch (_: IllegalStateException) {
                        result.error("worker_start_failed", "누리 명령 수신을 시작하지 못했습니다.", null)
                    }
                }
                "commandWorkerStatus" -> result.success(CommandWorkerService.status)
                "status" -> {
                    VoiceWaitService.reconcile(this)
                    result.success(getSharedPreferences("nuri_voice", MODE_PRIVATE).getString("status", "꺼짐"))
                }
                "active" -> {
                    VoiceWaitService.reconcile(this)
                    result.success(getSharedPreferences("nuri_voice", MODE_PRIVATE).getBoolean("active", false))
                }
                "mode" -> result.success(getSharedPreferences("nuri_voice", MODE_PRIVATE).getString("mode", "standby"))
                "setMode" -> {
                    val mode = call.argument<String>("mode")
                    if (mode != "standby" && mode != "memo") {
                        result.error("invalid_mode", "모드를 확인해 주세요.", null)
                    } else {
                        getSharedPreferences("nuri_voice", MODE_PRIVATE).edit().putString("mode", mode).apply()
                        if (VoiceWaitService.isRunning(this)) {
                            startService(Intent(this, VoiceWaitService::class.java).setAction(VoiceWaitService.ACTION_MODE_CHANGED))
                        }
                        VoiceWidget.refresh(this)
                        result.success(null)
                    }
                }
                "listMemos" -> {
                    try { MemoStore(this).use { result.success(it.list()) } }
                    catch (_: Exception) { result.error("memo_read_failed", "메모를 읽지 못했습니다.", null) }
                }
                "deleteMemo" -> {
                    val id = call.argument<Number>("id")?.toLong()
                    if (id == null || id <= 0) result.error("invalid_id", "메모를 찾을 수 없습니다.", null)
                    else try { MemoStore(this).use { result.success(it.delete(id)) } }
                    catch (_: Exception) { result.error("memo_delete_failed", "메모를 삭제하지 못했습니다.", null) }
                }
                "start" -> startListening(result)
                "stop" -> { stopListening(); result.success(null) }
                "outputStatus" -> result.success(getSharedPreferences("nuri_voice", MODE_PRIVATE).getBoolean("output_enabled", false))
                "setOutput" -> {
                    val enabled = call.argument<Boolean>("enabled") == true
                    getSharedPreferences("nuri_voice", MODE_PRIVATE).edit().putBoolean("output_enabled", enabled).apply()
                    if (!enabled) {
                        VoiceOutput.stop()
                        CustomVoice.stop()
                    }
                    VoiceWidget.refresh(this)
                    result.success(null)
                }
                "speak" -> {
                    val text = call.argument<String>("text").orEmpty()
                    val replay = call.argument<Boolean>("replay") == true && call.argument<Boolean>("assistant") == true
                    val outputEnabled = getSharedPreferences("nuri_voice", MODE_PRIVATE).getBoolean("output_enabled", false)
                    if ((outputEnabled || replay) && text.isNotBlank()) {
                        if (call.argument<Boolean>("assistant") == true) {
                            CustomVoice.speak(applicationContext, text, localQwen = true, answerId = call.argument<String>("answerId")) { error ->
                                if (error == null) result.success(null)
                                else result.error("synthesis_failed", error, null)
                            }
                            return@setMethodCallHandler
                        }
                        if (CustomVoice.selected(this)) {
                            CustomVoice.speak(applicationContext, text) { error ->
                                if (error == null) result.success(null)
                                else result.error("synthesis_failed", error, null)
                            }
                            return@setMethodCallHandler
                        }
                        VoiceOutput.speak(applicationContext, text)
                    }
                    result.success(null)
                }
                "retainAssistantVoice" -> {
                    CustomVoice.retainAssistantVoice(applicationContext, call.argument<List<String>>("ids").orEmpty())
                    result.success(null)
                }
                "previewAssistant" -> {
                    VoiceOutput.stop()
                    CustomVoice.speak(applicationContext, "안녕하세요. 도우미입니다.", localQwen = true) { error ->
                        if (error == null) result.success(null)
                        else result.error("preview_failed", error, null)
                    }
                }
                "peekCommand" -> result.success(getSharedPreferences("nuri_voice", MODE_PRIVATE).getString("command", null))
                "clearCommand" -> {
                    val prefs = getSharedPreferences("nuri_voice", MODE_PRIVATE)
                    if (prefs.getString("command", null) == call.argument<String>("text")) prefs.edit().remove("command").apply()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
        MethodChannel(engine.dartExecutor.binaryMessenger, "mynuri/custom_voice").setMethodCallHandler { call, result ->
            fun finish(error: String?) {
                if (error == null) result.success(null)
                else result.error("custom_voice_failed", error, null)
            }
            when (call.method) {
                "status" -> result.success(mapOf(
                    "modelReady" to CustomVoice.modelReady(this),
                    "hasProfile" to CustomVoice.hasProfile(this),
                    "selected" to CustomVoice.selected(this),
                ))
                "install" -> CustomVoice.install(applicationContext, ::finish)
                "import" -> {
                    if (voiceImportResult != null) {
                        finish("이미 파일 선택 화면이 열려 있습니다.")
                    } else {
                        try {
                            voiceImportResult = result
                            startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                                addCategory(Intent.CATEGORY_OPENABLE)
                                type = "audio/*"
                            }, 842)
                        } catch (e: Exception) {
                            voiceImportResult = null
                            finish("오디오 파일 선택 화면을 열지 못했습니다.")
                        }
                    }
                }
                "setEnabled" -> {
                    try {
                        CustomVoice.useCustom(this, call.argument<Boolean>("enabled") == true)
                        VoiceOutput.stop()
                        finish(null)
                    } catch (e: IllegalArgumentException) { finish(e.message) }
                }
                "preview" -> {
                    VoiceOutput.stop()
                    CustomVoice.speak(applicationContext, "안녕하세요. 모바일 누리입니다.", done = ::finish)
                }
                else -> result.notImplemented()
            }
        }
        MethodChannel(engine.dartExecutor.binaryMessenger, "mynuri/updater").setMethodCallHandler { call, result ->
            when (call.method) {
                "canInstall" -> result.success(
                    Build.VERSION.SDK_INT < Build.VERSION_CODES.O || packageManager.canRequestPackageInstalls()
                )
                "openInstallSettings" -> {
                    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
                        result.success(null)
                        return@setMethodCallHandler
                    }
                    try {
                        startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
                        result.success(null)
                    } catch (e: Exception) {
                        result.error("settings_failed", "설정 화면을 열지 못했습니다.", null)
                    }
                }
                "installApk" -> {
                    val path = call.argument<String>("path")
                    if (path.isNullOrBlank()) {
                        result.error("invalid_path", "설치할 파일이 없습니다.", null)
                        return@setMethodCallHandler
                    }
                    try {
                        val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", File(path))
                        val intent = Intent(Intent.ACTION_VIEW)
                            .setDataAndType(uri, "application/vnd.android.package-archive")
                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
                        startActivity(intent)
                        result.success(null)
                    } catch (e: Exception) {
                        result.error("install_failed", "설치 화면을 열지 못했습니다.", null)
                    }
                }
                "installManagedUpdate" -> {
                    val apk = call.argument<String>("path")?.let(::File)
                    if (apk == null) {
                        result.error("invalid_path", "설치할 파일이 없습니다.", null)
                        return@setMethodCallHandler
                    }
                    Thread {
                        val error = runCatching { installManagedUpdate(apk) }.exceptionOrNull()
                        runOnUiThread {
                            if (error == null) result.success(null)
                            else result.error("install_failed", error.message ?: "업데이트 설치 실패", null)
                        }
                    }.start()
                }
                else -> result.notImplemented()
            }
        }
    }

    @Suppress("DEPRECATION")
    private fun installManagedUpdate(apk: File) {
        check(apk.canonicalFile.parentFile == File(cacheDir, "updates").canonicalFile && apk.isFile)
        val candidate = packageManager.getPackageArchiveInfo(apk.path, PackageManager.GET_META_DATA)
        val installed = packageManager.getPackageInfo(packageName, PackageManager.GET_META_DATA)
        val roleKey = "com.example.mynuri.UPDATE_ROLE"
        val role = installed.applicationInfo?.metaData?.getString(roleKey)
        check(candidate != null && candidate.packageName == packageName && role in listOf("nuri", "assistant") &&
            candidate.applicationInfo?.metaData?.getString(roleKey) == role &&
            androidx.core.content.pm.PackageInfoCompat.getLongVersionCode(candidate) >
            androidx.core.content.pm.PackageInfoCompat.getLongVersionCode(installed)
        ) { "이 기기용 새 업데이트가 아닙니다." }
        val installer = packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        params.setAppPackageName(packageName)
        params.setSize(apk.length())
        if (Build.VERSION.SDK_INT >= 31) {
            params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = installer.createSession(params)
        try {
            installer.openSession(id).use { session ->
                session.openWrite("base.apk", 0, apk.length()).use { output ->
                    apk.inputStream().use { it.copyTo(output) }
                    session.fsync(output)
                }
                val callback = PendingIntent.getBroadcast(this, id,
                    Intent(this, UpdateInstallResult::class.java),
                    PendingIntent.FLAG_UPDATE_CURRENT or if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
                session.commit(callback.intentSender)
            }
        } catch (error: Exception) {
            installer.abandonSession(id)
            throw error
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != 842) return
        val result = voiceImportResult ?: return
        voiceImportResult = null
        val uri = data?.data
        if (resultCode != RESULT_OK || uri == null) {
            result.success(false)
            return
        }
        CustomVoice.import(applicationContext, uri) { error ->
            if (error == null) result.success(true)
            else result.error("voice_import_failed", error, null)
        }
    }

    private fun startListening(result: MethodChannel.Result?) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            result?.error("unsupported", "Android 12 이상이 필요합니다.", null)
            VoiceWaitService.setState(this, false, "Android 12 이상이 필요합니다")
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            waitingResult = result
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 841)
            return
        }
        try {
            startForegroundService(Intent(this, VoiceWaitService::class.java))
            result?.success(null)
        } catch (e: Exception) {
            VoiceWaitService.setState(this, false, "음성 대기를 시작하지 못했습니다")
            result?.error("start_failed", "음성 대기를 시작하지 못했습니다.", null)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != 841) return
        val result = waitingResult
        waitingResult = null
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) startListening(result)
        else {
            VoiceWaitService.setState(this, false, "마이크 권한이 필요합니다")
            result?.error("permission", "마이크 권한이 필요합니다.", null)
        }
    }

    private fun stopListening() {
        stopService(Intent(this, VoiceWaitService::class.java))
        VoiceWaitService.setState(this, false, "꺼짐")
    }
}

package com.example.mynuri

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.BroadcastReceiver
import android.content.Context
import android.os.Build
import android.os.IBinder
import android.util.Log
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class CommandWorkerService : Service() {
    companion object {
        internal const val CHANNEL = "nuri_command_worker"
        private const val TAG = "NuriCommandWorker"
        internal const val ADDRESS = "address"
        @Volatile var status: String? = null
            private set
    }

    private val executor = Executors.newSingleThreadScheduledExecutor()
    @Volatile private var address = ""
    private var lastAddress = ""
    private var command: JSONObject? = null
    private var submitted = false

    override fun onCreate() {
        super.onCreate()
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(CHANNEL, "누리 명령 수신", NotificationManager.IMPORTANCE_LOW),
            )
            Notification.Builder(this, CHANNEL)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        startForeground(402, builder.setSmallIcon(android.R.drawable.ic_menu_send)
            .setContentTitle("누리 명령 수신 중")
            .setContentText("화면이 꺼져도 도우미의 요청을 확인합니다.")
            .setContentIntent(open).setOngoing(true).build())
        status = "도우미 요청 대기 중"
        executor.scheduleWithFixedDelay(::poll, 1, 3, TimeUnit.SECONDS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val preferences = getSharedPreferences(CHANNEL, MODE_PRIVATE)
        val requested = intent?.getStringExtra(ADDRESS) ?: preferences.getString(ADDRESS, "").orEmpty()
        val uri = runCatching { URI(requested) }.getOrNull()
        if (uri == null || uri.scheme !in listOf("http", "https") || uri.host == null ||
            uri.rawUserInfo != null || uri.rawQuery != null || uri.rawFragment != null ||
            uri.rawPath !in listOf("", "/")) {
            stopSelf(startId)
            return START_NOT_STICKY
        }
        address = requested
        preferences.edit().putString(ADDRESS, requested).apply()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        address = ""
        executor.shutdownNow()
        status = null
        super.onDestroy()
    }

    private fun poll() {
        val base = address
        if (base.isEmpty()) return
        if (lastAddress != base) {
            lastAddress = base
            command = null
            submitted = false
        }
        try {
            val job = command ?: request(base, "/v1/worker/take", JSONObject())
                .optJSONObject("command")?.also {
                    command = it
                    status = "도우미 요청 받음"
                    Log.i(TAG, "Received ${it.getString("request_id")}")
                } ?: return
            val id = job.getString("request_id")
            if (!submitted) {
                request(base, "/v1/commands", job)
                submitted = true
                status = "도우미 요청을 PC 처리기에 전달했습니다"
            }
            val response = request(base, "/v1/commands/$id")
            val state = response.getString("status")
            if (state !in listOf("completed", "failed", "unknown")) return
            val text = response.optJSONObject("result")?.optString("text")
            val result = JSONObject().put("status", if (state == "completed" && text == null) "failed" else state)
            if (text != null) result.put("text", text)
            request(base, "/v1/worker/$id/result", result)
            command = null
            submitted = false
            status = if (state == "completed" && text != null) "도우미에게 작업 결과를 전달했습니다"
                else "도우미에게 작업 상태를 전달했습니다"
            Log.i(TAG, "Returned $id ($state)")
        } catch (error: Exception) {
            // The relay returns the same command after a disconnect or process restart.
            Log.d(TAG, "Retrying after ${error.javaClass.simpleName}")
        }
    }

    private fun request(base: String, path: String, payload: JSONObject? = null): JSONObject {
        val connection = URI(base).resolve(path).toURL().openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 8_000
            connection.readTimeout = 12_000
            connection.instanceFollowRedirects = false
            if (payload != null) {
                val bytes = payload.toString().toByteArray(Charsets.UTF_8)
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setFixedLengthStreamingMode(bytes.size)
                connection.outputStream.use { it.write(bytes) }
            }
            check(connection.responseCode in 200..299)
            return connection.inputStream.bufferedReader(Charsets.UTF_8).use { JSONObject(it.readText()) }
        } finally {
            connection.disconnect()
        }
    }
}

class RestoreCommandWorker : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val saved = context.getSharedPreferences(CommandWorkerService.CHANNEL, Context.MODE_PRIVATE)
            .getString(CommandWorkerService.ADDRESS, null)
        if (saved.isNullOrBlank()) return
        val worker = Intent(context, CommandWorkerService::class.java)
            .putExtra(CommandWorkerService.ADDRESS, saved)
        try {
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(worker)
            else context.startService(worker)
        } catch (error: IllegalStateException) {
            Log.w("NuriCommandWorker", "Android deferred command worker restart", error)
        } catch (error: SecurityException) {
            Log.w("NuriCommandWorker", "Android blocked command worker restart", error)
        }
    }
}

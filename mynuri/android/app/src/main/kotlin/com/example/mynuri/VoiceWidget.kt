package com.example.mynuri

import com.example.mynuri.moss.CustomVoice

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews

class VoiceWidget : AppWidgetProvider() {
    override fun onReceive(context: Context, intent: Intent) {
        super.onReceive(context, intent)
        if (intent.action == "com.example.mynuri.TOGGLE_OUTPUT") {
            val prefs = context.getSharedPreferences("nuri_voice", Context.MODE_PRIVATE)
            val enabled = !prefs.getBoolean("output_enabled", false)
            prefs.edit().putBoolean("output_enabled", enabled).apply()
            if (!enabled) {
                VoiceOutput.stop()
                CustomVoice.stop()
            }
            refresh(context)
        }
    }

    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        refresh(context)
    }

    companion object {
        fun refresh(context: Context) {
            val manager = AppWidgetManager.getInstance(context)
            val ids = manager.getAppWidgetIds(ComponentName(context, VoiceWidget::class.java))
            val active = context.getSharedPreferences("nuri_voice", Context.MODE_PRIVATE)
                .getBoolean("active", false)
            val outputEnabled = context.getSharedPreferences("nuri_voice", Context.MODE_PRIVATE).getBoolean("output_enabled", false)
            val memoMode = context.getSharedPreferences("nuri_voice", Context.MODE_PRIVATE).getString("mode", "standby") == "memo"
            for (id in ids) {
                val views = RemoteViews(context.packageName, R.layout.voice_widget)
                views.setTextViewText(R.id.voice_widget_text, if (memoMode) { if (active) "메모 기록 끄기" else "메모 기록 켜기" } else { if (active) "음성 입력 끄기" else "음성 입력 켜기" })
                val intent = Intent(context, WidgetControlActivity::class.java).apply {
                    action = "com.example.mynuri.TOGGLE_VOICE"
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
                }
                val pending = PendingIntent.getActivity(context, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
                views.setOnClickPendingIntent(R.id.voice_widget_text, pending)
                views.setTextViewText(R.id.voice_output_text, if (outputEnabled) "음성 출력 끄기" else "음성 출력 켜기")
                val outputIntent = Intent(context, VoiceWidget::class.java).apply { action = "com.example.mynuri.TOGGLE_OUTPUT" }
                val outputPending = PendingIntent.getBroadcast(context, 1, outputIntent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
                views.setOnClickPendingIntent(R.id.voice_output_text, outputPending)
                manager.updateAppWidget(id, views)
            }
        }
    }
}

package com.example.mynuri

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle

class WidgetControlActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        VoiceWaitService.reconcile(this)
        if (getSharedPreferences("nuri_voice", MODE_PRIVATE).getBoolean("active", false)) {
            stopService(Intent(this, VoiceWaitService::class.java))
            VoiceWaitService.setState(this, false, "꺼짐")
            finish()
        } else if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            VoiceWaitService.setState(this, false, "Android 12 이상이 필요합니다")
            finish()
        } else if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 841)
        } else start()
    }

    private fun start() {
        try {
            startForegroundService(Intent(this, VoiceWaitService::class.java))
        } catch (_: Exception) {
            VoiceWaitService.setState(this, false, "음성 대기를 시작하지 못했습니다")
        }
        finish()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != 841) return
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) start()
        else { VoiceWaitService.setState(this, false, "마이크 권한이 필요합니다"); finish() }
    }
}

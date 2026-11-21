package com.example.mynuri

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.util.Log
import androidx.core.content.ContextCompat

class UpdateInstallResult : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            val preferences = context.getSharedPreferences("nuri_command_worker", Context.MODE_PRIVATE)
            val address = preferences.getString("address", null)
            if (!address.isNullOrBlank()) {
                val worker = Intent(context, CommandWorkerService::class.java).putExtra("address", address)
                runCatching { ContextCompat.startForegroundService(context, worker) }
                    .onFailure { Log.w("NuriUpdate", "Could not restart worker", it) }
            }
            return
        }
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        Log.i("NuriUpdate", "Android installer result: $status")
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            @Suppress("DEPRECATION")
            val confirmation = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
            if (confirmation != null) {
                pendingConfirmation = confirmation
                if (MainActivity.updateActivityVisible) showConfirmation(context)
            }
        }
    }

    companion object {
        @Volatile private var pendingConfirmation: Intent? = null
        fun showConfirmation(context: Context) {
            val next = pendingConfirmation ?: return
            pendingConfirmation = null
            runCatching { context.startActivity(next.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                .onFailure { pendingConfirmation = next }
        }
    }
}

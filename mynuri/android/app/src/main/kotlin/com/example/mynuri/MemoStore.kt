package com.example.mynuri

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper

/** 인식한 메모를 앱 전용 저장소에 보관한다. */
class MemoStore(context: Context) : SQLiteOpenHelper(context, "memos.db", null, 1) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE memos (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_at INTEGER NOT NULL)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    fun add(text: String) {
        val values = ContentValues().apply {
            put("text", text)
            put("created_at", System.currentTimeMillis())
        }
        writableDatabase.insertOrThrow("memos", null, values)
    }

    fun list(): List<Map<String, Any>> {
        val result = mutableListOf<Map<String, Any>>()
        readableDatabase.query("memos", arrayOf("id", "text", "created_at"), null, null, null, null, "id DESC").use { cursor ->
            while (cursor.moveToNext()) {
                result.add(mapOf("id" to cursor.getLong(0), "text" to cursor.getString(1), "createdAt" to cursor.getLong(2)))
            }
        }
        return result
    }

    fun delete(id: Long): Boolean = writableDatabase.delete("memos", "id = ?", arrayOf(id.toString())) > 0
}

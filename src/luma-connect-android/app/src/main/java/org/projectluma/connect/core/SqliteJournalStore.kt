package org.projectluma.connect.core

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.projectluma.connect.protocol.Json
import org.projectluma.connect.protocol.JournalStore
import org.projectluma.connect.protocol.Peer
import org.projectluma.connect.protocol.RequestRecord

/** Durable journal in the app's no-backup database. Transactions are exclusive and serialized in-process. */
class SqliteJournalStore(context: Context) : JournalStore {
    private val lock = Any()
    private val helper = object : SQLiteOpenHelper(context, "continuity.db", null, 1) {
        override fun onConfigure(db: SQLiteDatabase) {
            db.enableWriteAheadLogging()
        }

        override fun onCreate(db: SQLiteDatabase) {
            db.execSQL("CREATE TABLE control(singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL)")
            db.execSQL(
                """CREATE TABLE peers(pin TEXT PRIMARY KEY, epoch TEXT NOT NULL, account TEXT, incoming TEXT NOT NULL,
                outgoing TEXT NOT NULL, revoked INTEGER NOT NULL, name TEXT NOT NULL, certificate TEXT NOT NULL,
                host TEXT, port INTEGER)""",
            )
            db.execSQL("CREATE TABLE epochs(pin TEXT NOT NULL, epoch TEXT NOT NULL, PRIMARY KEY(pin, epoch))")
            db.execSQL(
                """CREATE TABLE requests(pin TEXT NOT NULL, epoch TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL,
                state TEXT NOT NULL, response TEXT, expires INTEGER NOT NULL, PRIMARY KEY(pin, epoch, id))""",
            )
        }

        override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit
    }

    private val db: SQLiteDatabase get() = helper.writableDatabase

    override fun <T> transaction(block: () -> T): T = synchronized(lock) {
        val database = db
        database.beginTransaction()
        try {
            val result = block()
            database.setTransactionSuccessful()
            result
        } finally {
            database.endTransaction()
        }
    }

    fun setEnabled(enabled: Boolean) = transaction {
        db.execSQL("INSERT OR REPLACE INTO control(singleton, enabled) VALUES(1, ?)", arrayOf<Any?>(if (enabled) 1 else 0))
    }

    override fun enabled(): Boolean =
        db.rawQuery("SELECT enabled FROM control WHERE singleton=1", null).use { !it.moveToFirst() || it.getInt(0) == 1 }

    override fun peer(pin: String): Peer? =
        db.rawQuery("SELECT * FROM peers WHERE pin=?", arrayOf(pin)).use { if (it.moveToFirst()) read(it) else null }

    override fun peers(): List<Peer> =
        db.rawQuery("SELECT * FROM peers ORDER BY name", null).use { cursor ->
            buildList { while (cursor.moveToNext()) add(read(cursor)) }
        }

    private fun read(cursor: android.database.Cursor): Peer {
        fun text(name: String) = cursor.getString(cursor.getColumnIndexOrThrow(name))
        fun set(name: String) = (Json.parse(text(name)) as List<*>).map { it as String }.toSet()
        val portIndex = cursor.getColumnIndexOrThrow("port")
        return Peer(
            pin = text("pin"), epoch = text("epoch"), account = text("account"),
            incoming = set("incoming"), outgoing = set("outgoing"),
            revoked = cursor.getInt(cursor.getColumnIndexOrThrow("revoked")) == 1,
            name = text("name"), certificatePem = text("certificate"), host = text("host"),
            port = if (cursor.isNull(portIndex)) null else cursor.getInt(portIndex),
        )
    }

    override fun putPeer(peer: Peer) {
        db.insertWithOnConflict("peers", null, ContentValues().apply {
            put("pin", peer.pin); put("epoch", peer.epoch); put("account", peer.account)
            put("incoming", Json.encodeToString(peer.incoming.sorted())); put("outgoing", Json.encodeToString(peer.outgoing.sorted()))
            put("revoked", if (peer.revoked) 1 else 0); put("name", peer.name); put("certificate", peer.certificatePem)
            put("host", peer.host); put("port", peer.port)
        }, SQLiteDatabase.CONFLICT_REPLACE)
    }

    override fun deletePeer(pin: String) {
        db.delete("peers", "pin=?", arrayOf(pin))
    }

    override fun epochUsed(pin: String, epoch: String): Boolean =
        db.rawQuery("SELECT 1 FROM epochs WHERE pin=? AND epoch=?", arrayOf(pin, epoch)).use { it.moveToFirst() }

    override fun recordEpoch(pin: String, epoch: String) {
        db.execSQL("INSERT INTO epochs(pin, epoch) VALUES(?, ?)", arrayOf<Any?>(pin, epoch))
    }

    override fun request(pin: String, epoch: String, id: String): RequestRecord? =
        db.rawQuery("SELECT digest, state, response, expires FROM requests WHERE pin=? AND epoch=? AND id=?", arrayOf(pin, epoch, id)).use {
            if (it.moveToFirst()) RequestRecord(it.getString(0), it.getString(1), it.getString(2), it.getLong(3)) else null
        }

    override fun insertRequest(pin: String, epoch: String, id: String, record: RequestRecord) {
        db.execSQL(
            "INSERT INTO requests(pin, epoch, id, digest, state, response, expires) VALUES(?, ?, ?, ?, ?, ?, ?)",
            arrayOf<Any?>(pin, epoch, id, record.digest, record.state, record.response, record.expires),
        )
    }

    override fun completeRequest(pin: String, epoch: String, id: String, response: String) {
        db.execSQL("UPDATE requests SET state='complete', response=? WHERE pin=? AND epoch=? AND id=?", arrayOf<Any?>(response, pin, epoch, id))
    }

    override fun requestCount(pin: String): Int =
        db.rawQuery("SELECT count(*) FROM requests WHERE pin=?", arrayOf(pin)).use { it.moveToFirst(); it.getInt(0) }

    override fun pruneRequests(now: Long): Int = db.delete("requests", "expires < ?", arrayOf(now.toString()))

    fun forget(pin: String) = transaction {
        db.delete("requests", "pin=?", arrayOf(pin))
        db.delete("peers", "pin=?", arrayOf(pin))
    }
}

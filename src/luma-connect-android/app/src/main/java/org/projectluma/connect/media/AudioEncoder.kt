package org.projectluma.connect.media

import android.Manifest
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaCodec
import android.media.MediaCodecList
import android.media.MediaFormat
import android.media.MediaRecorder
import androidx.annotation.RequiresPermission
import org.projectluma.connect.protocol.MediaStream

/** Microphone to Opus, 48 kHz mono, for the phone-as-webcam session. Absent when the device has no Opus encoder. */
class AudioEncoder private constructor(private val record: AudioRecord, private val codec: MediaCodec, private val stream: MediaStream) {
    @Volatile private var running = true
    private val thread = Thread(::run, "luma-connect-audio")

    private fun run() {
        val pcm = ByteArray(960 * 2)
        val info = MediaCodec.BufferInfo()
        try {
            record.startRecording()
            while (running) {
                val read = record.read(pcm, 0, pcm.size)
                if (read <= 0) continue
                val input = codec.dequeueInputBuffer(10_000)
                if (input >= 0) {
                    codec.getInputBuffer(input)!!.apply { clear(); put(pcm, 0, read) }
                    codec.queueInputBuffer(input, 0, read, System.nanoTime() / 1000, 0)
                }
                while (true) {
                    val output = codec.dequeueOutputBuffer(info, 0)
                    if (output < 0) break
                    val buffer = codec.getOutputBuffer(output)
                    if (buffer != null && info.size > 0) {
                        val data = ByteArray(info.size).also { buffer.position(info.offset); buffer.get(it) }
                        if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) opusHead(data)?.let(stream::audioConfig) else stream.audio(info.presentationTimeUs, data)
                    }
                    codec.releaseOutputBuffer(output, false)
                }
            }
        } catch (_: Exception) {
            running = false
        }
    }

    fun stop() {
        running = false
        runCatching { thread.join(500) }
        runCatching { record.stop(); record.release() }
        runCatching { codec.stop(); codec.release() }
    }

    companion object {
        /**
         * The contract sends a raw RFC 7845 OpusHead. Android's encoder wraps it as
         * "AOPUSHDR" + little-endian length + OpusHead, followed by delay markers.
         */
        fun opusHead(config: ByteArray): ByteArray? {
            val marker = "AOPUSHDR".toByteArray()
            if (config.size >= 8 && config.copyOfRange(0, 8).contentEquals("OpusHead".toByteArray())) return config
            if (config.size < 16 || !config.copyOfRange(0, 8).contentEquals(marker)) return null
            var length = 0L
            for (i in 7 downTo 0) length = (length shl 8) or (config[8 + i].toLong() and 0xff)
            if (length !in 19L..(config.size - 16L)) return null
            val head = config.copyOfRange(16, 16 + length.toInt())
            return head.takeIf { it.copyOfRange(0, 8).contentEquals("OpusHead".toByteArray()) }
        }

        @RequiresPermission(Manifest.permission.RECORD_AUDIO)
        fun start(stream: MediaStream): AudioEncoder? {
            val format = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, 48_000, 1).apply {
                setInteger(MediaFormat.KEY_BIT_RATE, 64_000)
            }
            val name = MediaCodecList(MediaCodecList.REGULAR_CODECS).findEncoderForFormat(format) ?: return null
            val codec = MediaCodec.createByCodecName(name).apply { configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE); start() }
            val size = maxOf(AudioRecord.getMinBufferSize(48_000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), 960 * 4)
            val record = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, 48_000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size)
            return AudioEncoder(record, codec, stream).also { it.thread.start() }
        }
    }
}

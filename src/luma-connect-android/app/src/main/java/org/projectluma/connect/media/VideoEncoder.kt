package org.projectluma.connect.media

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.os.Build
import android.os.Bundle
import android.view.Surface
import org.projectluma.connect.protocol.MediaStream

/**
 * Low-latency surface encoder (AV1, VP9, VP8, H.264 or H.265) feeding a [MediaStream]: realtime priority,
 * constant bitrate, frequent sync frames, and a key frame on demand.
 */
class VideoEncoder(width: Int, height: Int, fps: Int, bitrate: Int, private val stream: MediaStream, choice: Codecs.Choice) {
    private val codec = MediaCodec.createByCodecName(choice.encoder)
    val surface: Surface
    @Volatile private var running = true
    private val thread: Thread

    init {
        val format = MediaFormat.createVideoFormat(Codecs.MIME.getValue(choice.codec), width, height).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, bitrate)
            setInteger(MediaFormat.KEY_FRAME_RATE, fps)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2)
            setInteger(MediaFormat.KEY_BITRATE_MODE, MediaCodecInfo.EncoderCapabilities.BITRATE_MODE_CBR)
            setInteger(MediaFormat.KEY_PRIORITY, 0)
            // Repeat the last frame when the image is static so the receiver never stalls.
            setLong(MediaFormat.KEY_REPEAT_PREVIOUS_FRAME_AFTER, 100_000)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
        }
        codec.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        surface = codec.createInputSurface()
        codec.start()
        thread = Thread(::drain, "luma-connect-video").apply { start() }
    }

    fun requestKeyFrame() {
        runCatching { codec.setParameters(Bundle().apply { putInt(MediaCodec.PARAMETER_KEY_REQUEST_SYNC_FRAME, 0) }) }
    }

    private fun drain() {
        val info = MediaCodec.BufferInfo()
        try {
            while (running) {
                val index = codec.dequeueOutputBuffer(info, 50_000)
                if (index < 0) continue
                val buffer = codec.getOutputBuffer(index)
                if (buffer != null && info.size > 0) {
                    val data = ByteArray(info.size)
                    buffer.position(info.offset)
                    buffer.get(data)
                    when {
                        info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0 -> stream.codecConfig(data)
                        else -> stream.video(info.presentationTimeUs, data, info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME != 0)
                    }
                }
                codec.releaseOutputBuffer(index, false)
                if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) break
            }
        } catch (_: Exception) {
            running = false
        } finally {
            onStopped?.invoke()
        }
    }

    @Volatile var onStopped: (() -> Unit)? = null

    fun stop() {
        running = false
        runCatching { thread.join(500) }
        runCatching { codec.stop() }
        runCatching { codec.release() }
        runCatching { surface.release() }
    }
}

package com.sirena.nina.companion.ui

import android.net.Uri
import android.widget.VideoView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import com.sirena.nina.companion.R

/** Full-screen intro; advances to the main app when playback completes or errors. */
@Composable
fun SplashVideo(onFinished: () -> Unit) {
    val ctx = LocalContext.current
    val pkg = ctx.packageName
    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black),
    ) {
        AndroidView(
            factory = { c ->
                VideoView(c).apply {
                    val uri = Uri.parse("android.resource://$pkg/${R.raw.nina_splash}")
                    setVideoURI(uri)
                    setOnCompletionListener { onFinished() }
                    setOnErrorListener { _, _, _ ->
                        onFinished()
                        true
                    }
                    start()
                }
            },
            modifier = Modifier.fillMaxSize(),
        )
    }
}

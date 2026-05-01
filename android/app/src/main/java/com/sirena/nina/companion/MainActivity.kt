package com.sirena.nina.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.ViewModelProvider
import com.sirena.nina.companion.ui.NinaApp
import com.sirena.nina.companion.ui.theme.SirenaTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val vm = ViewModelProvider(
            this,
            ViewModelProvider.AndroidViewModelFactory.getInstance(application),
        )[CompanionViewModel::class.java]
        setContent {
            SirenaTheme {
                NinaApp(vm)
            }
        }
    }
}

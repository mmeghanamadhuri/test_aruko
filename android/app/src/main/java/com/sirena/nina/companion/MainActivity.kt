package com.sirena.nina.companion

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.ViewModelProvider
import com.sirena.nina.companion.ui.NinaApp
import com.sirena.nina.companion.ui.theme.SirenaTheme

class MainActivity : ComponentActivity() {

    private lateinit var vm: CompanionViewModel
    private var networkCallback: ConnectivityManager.NetworkCallback? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        vm = ViewModelProvider(
            this,
            ViewModelProvider.AndroidViewModelFactory.getInstance(application),
        )[CompanionViewModel::class.java]

        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        networkCallback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                vm.refreshStatus()
            }
        }
        cm.registerDefaultNetworkCallback(networkCallback!!)

        setContent {
            SirenaTheme {
                NinaApp(vm)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        if (::vm.isInitialized) vm.refreshStatus()
    }

    override fun onDestroy() {
        networkCallback?.let { cb ->
            try {
                (getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager)
                    .unregisterNetworkCallback(cb)
            } catch (_: Exception) {
                // ignore
            }
        }
        networkCallback = null
        super.onDestroy()
    }
}

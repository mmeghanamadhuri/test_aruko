package com.sirena.nina.companion.ui

import android.content.ClipData
import android.content.Intent
import android.provider.Settings
import androidx.core.content.FileProvider
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.foundation.layout.width
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sirena.nina.companion.R      
import com.sirena.nina.companion.CompanionUiState
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.StatusUi
import com.sirena.nina.companion.data.Prefs
import com.sirena.nina.companion.util.NinaFileLogger
import com.sirena.nina.companion.util.NinaLog
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NinaApp(vm: CompanionViewModel) {
    val state by vm.state.collectAsStateWithLifecycle()
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var showNinaConsole by rememberSaveable { mutableStateOf(false) }
    val snack = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    Box(Modifier.fillMaxSize()) {
        Scaffold(
        snackbarHost = { SnackbarHost(snack) },
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        "Sirena UI",
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    titleContentColor = MaterialTheme.colorScheme.onPrimary,
                ),
            )
        },
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    selected = tab == 0,
                    onClick = {
                        NinaLog.tap("Main", "bottom_nav", "Home")
                        tab = 0
                    },
                    icon = { Icon(Icons.Default.Home, null) },
                    label = { Text("Home") },
                )
                NavigationBarItem(
                    selected = tab == 1,
                    onClick = {
                        NinaLog.tap("Main", "bottom_nav", "Networks")
                        tab = 1
                    },
                    icon = { Icon(Icons.Default.Wifi, null) },
                    label = { Text("Networks") },
                )
                NavigationBarItem(
                    selected = tab == 2,
                    onClick = {
                        NinaLog.tap("Main", "bottom_nav", "Setup")
                        tab = 2
                    },
                    icon = { Icon(Icons.Default.Settings, null) },
                    label = { Text("Setup") },
                )
            }
        },
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize(),
        ) {
            when (tab) {
                0 ->
                    HomeTab(
                        vm = vm,
                        snack = snack,
                        onOpenNinaConsole = { showNinaConsole = true },
                        onGoNetworks = { tab = 1 },
                        onGoSetup = { tab = 2 },
                    )
                1 -> NetworksTab(state = state, vm = vm, snack = snack)
                2 -> SetupTab(vm = vm, snack = snack)
            }
        }
        }

        if (showNinaConsole) {
            NinaConsoleScreen(
                vm = vm,
                state = state,
                onBack = { showNinaConsole = false },
                modifier = Modifier.fillMaxSize(),
            )
        }
    }

    LaunchedEffect(showNinaConsole) {
        vm.notifyRobotConsoleVisibility(showNinaConsole)
    }

    LaunchedEffect(state) {
        if (state is CompanionUiState.Error) {
            snack.showSnackbar((state as CompanionUiState.Error).text)
        }
    }
}

@Composable
private fun HomeTab(
    vm: CompanionViewModel,
    snack: SnackbarHostState,
    onOpenNinaConsole: () -> Unit,
    onGoNetworks: () -> Unit,
    onGoSetup: () -> Unit,
) {
    val jetsonLink by vm.jetsonLink.collectAsStateWithLifecycle()

    Box(
        Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp, vertical = 20.dp),
    ) {
        val jetsonOnline = jetsonLink.isOnline
        Text(
            text = if (jetsonOnline) "Jetson online" else "Jetson offline",
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Medium,
            color =
                if (jetsonOnline) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
            modifier = Modifier.align(Alignment.TopEnd),
        )
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
            modifier =
                Modifier
                    .fillMaxWidth()
                    .align(Alignment.Center),
        ) {
            Image(
                painter = painterResource(R.drawable.sirena_technologies_logo_color),
                contentDescription = null,
                modifier =
                    Modifier
                        .heightIn(max = 112.dp)
                        .widthIn(max = 280.dp),
                contentScale = ContentScale.Fit,
            )
            Text(
                "Sirena UI",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
            )
            Text(
                "Connection and diagnostics live under Setup.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 16.dp),
            )
            Button(
                onClick = {
                    NinaLog.tap("Home", "open_robot_ui")
                    onOpenNinaConsole()
                },
                modifier =
                    Modifier
                        .widthIn(min = 220.dp, max = 400.dp)
                        .fillMaxWidth(0.85f),
            ) {
                Text("Nina")
            }
            Row(
                horizontalArrangement = Arrangement.spacedBy(4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(
                    onClick = {
                        NinaLog.tap("Home", "networks_shortcut")
                        onGoNetworks()
                    },
                ) {
                    Text("Networks")
                }
                TextButton(
                    onClick = {
                        NinaLog.tap("Home", "setup_shortcut")
                        onGoSetup()
                    },
                ) {
                    Text("Setup")
                }
            }
        }
    }
}

@Composable
private fun SetupDiagnosticsCard(snack: SnackbarHostState) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val logFile = NinaFileLogger.activeLogFile(ctx)
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f)),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Session log", fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.titleSmall)
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        NinaFileLogger.ACTIVE_LOG_NAME,
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.Medium,
                    )
                    Text(
                        "logs/ · app-private storage",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                OutlinedButton(
                    onClick = {
                        NinaLog.tap("Setup", "export_session_log")
                        if (!logFile.exists()) {
                            scope.launch {
                                snack.showSnackbar("No log file yet — use the app, then try again.")
                            }
                            return@OutlinedButton
                        }
                        val uri =
                            FileProvider.getUriForFile(
                                ctx,
                                "${ctx.packageName}.fileprovider",
                                logFile,
                            )
                        val send =
                            Intent(Intent.ACTION_SEND).apply {
                                type = "text/plain"
                                putExtra(Intent.EXTRA_STREAM, uri)
                                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                clipData = ClipData.newUri(ctx.contentResolver, "Session log", uri)
                            }
                        ctx.startActivity(Intent.createChooser(send, "Export session log"))
                    },
                ) {
                    Text("Export")
                }
            }
        }
    }
}

@Composable
private fun StatusGridCard(st: StatusUi) {
    val rows =
        buildList {
            add("Wi‑Fi role" to st.wifiRole)
            add("IPv4" to (st.ipv4 ?: "—"))
            add("User mode" to st.userMode)
            add("Boot window" to "${st.bootWaitRemainingSec}s")
            add("Client seen" to if (st.clientSeen) "Yes" else "—")
            st.apSsid?.let { add("AP SSID" to it) }
            if (!st.activeStaSsid.isNullOrBlank()) {
                add("STA SSID" to st.activeStaSsid!!)
                st.activeStaProfile?.let { add("NM profile" to it) }
            }
        }
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Jetson link", fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.titleSmall)
            rows.chunked(2).forEach { pair ->
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    pair.forEach { (label, value) ->
                        StatusStatCell(label, value, Modifier.weight(1f))
                    }
                    if (pair.size == 1) {
                        Spacer(Modifier.weight(1f))
                    }
                }
            }
            st.lastError?.let {
                Text(
                    "Last error: $it",
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

@Composable
private fun StatusStatCell(label: String, value: String, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.padding(vertical = 2.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun NetworksTab(
    state: CompanionUiState,
    vm: CompanionViewModel,
    snack: SnackbarHostState,
) {
    val scope = rememberCoroutineScope()
    Column(
        Modifier
            .fillMaxSize()
            .padding(16.dp),
    ) {
        Text(
            "Saved on Jetson",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            "Connect Jetson asks the robot to join that profile (STA). Then join the same Wi‑Fi on " +
                "this tablet and tap Refresh in Setup.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(12.dp))
        when (state) {
            is CompanionUiState.Ready -> {
                val list = state.status?.savedNetworks.orEmpty()
                if (list.isEmpty()) {
                    Text(
                        "No profiles yet. Add home Wi‑Fi under Setup.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        items(list, key = { it.uuid }) { net ->
                            Card(Modifier.fillMaxWidth()) {
                                Column(Modifier.padding(12.dp)) {
                                    Text(net.ssid, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                                    Text(
                                        net.uuid,
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                    Text(
                                        if (net.nmAutoconnect) "NM autoconnect: on" else "NM autoconnect: off",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                    Spacer(Modifier.height(8.dp))
                                    Row(
                                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                                        modifier = Modifier.fillMaxWidth(),
                                    ) {
                                        Button(
                                            onClick = {
                                                NinaLog.tap("Networks", "connect_jetson", net.ssid)
                                                vm.connectJetsonHome(net.ssid)
                                                scope.launch {
                                                    snack.showSnackbar(
                                                        "Jetson is connecting to “${net.ssid}”. " +
                                                            "When online, join that Wi‑Fi on this tablet, open the app, tap Refresh in Setup.",
                                                    )
                                                }
                                            },
                                            modifier = Modifier.weight(1f),
                                        ) {
                                            Text("Connect Jetson")
                                        }
                                        OutlinedButton(
                                            onClick = {
                                                NinaLog.tap("Networks", "delete_profile", net.ssid)
                                                vm.deleteProfile(net.id)
                                            },
                                            modifier = Modifier.weight(1f),
                                        ) {
                                            Text("Remove")
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            else -> Text("Connect to the robot first (Refresh in Setup).")
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SetupTab(vm: CompanionViewModel, snack: SnackbarHostState) {
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    val gatewayHint by vm.gatewayHint.collectAsStateWithLifecycle(null)
    val state by vm.state.collectAsStateWithLifecycle()
    val savedUrl by vm.savedDaemonUrl.collectAsStateWithLifecycle(Prefs.DEFAULT_BASE_URL)
    var urlDraft by remember { mutableStateOf<String?>(null) }
    val url = urlDraft ?: savedUrl
    var bearer by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var showToken by remember { mutableStateOf<String?>(null) }
    var ssid by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }

    if (showToken != null) {
        AlertDialog(
            onDismissRequest = { showToken = null },
            confirmButton = {
                TextButton(onClick = { showToken = null }) { Text("OK") }
            },
            title = { Text("Session token saved") },
            text = { Text("Stored for API calls. You can paste a token manually below if needed.") },
        )
    }

    val scrollState = rememberScrollState()

    @Composable
    fun WifiBlock() {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Home Wi‑Fi (Jetson)", fontWeight = FontWeight.Medium, style = MaterialTheme.typography.titleSmall)
            Text(
                "Saved on the Jetson (Network settings).",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedTextField(
                ssid,
                onValueChange = { ssid = it },
                label = { Text("SSID") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            OutlinedTextField(
                password,
                onValueChange = { password = it },
                label = { Text("Password") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(
                    onClick = {
                        NinaLog.tap("Setup", "save_wifi_credentials_only")
                        vm.saveHomeAndOptionallyConnect(ssid, password, connect = false)
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Save creds")
                }
                OutlinedButton(
                    onClick = {
                        NinaLog.tap("Setup", "save_wifi_and_connect_jetson")
                        vm.saveHomeAndOptionallyConnect(ssid, password, connect = true)
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Save & connect")
                }
            }
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedButton(
                    onClick = {
                        NinaLog.tap("Setup", "connect_jetson_first_saved")
                        vm.connectJetsonHome(null)
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Connect saved")
                }
                OutlinedButton(
                    onClick = {
                        NinaLog.tap("Setup", "force_ap_on_jetson")
                        vm.startApOnJetson()
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Force AP")
                }
            }
        }
    }

    @Composable
    fun DaemonAuthBlock() {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Daemon URL", fontWeight = FontWeight.Medium, style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(
                url,
                onValueChange = { urlDraft = it },
                label = { Text("http://gateway:8787") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Text(
                "Use Wi‑Fi gateway, not this tablet’s IP.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Button(
                onClick = {
                    NinaLog.tap("Setup", "save_and_test_daemon_url")
                    vm.saveBaseUrl(url)
                    vm.ping(url)
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Save & test")
            }
            Text("Auth", fontWeight = FontWeight.Medium, style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(
                bearer,
                onValueChange = { bearer = it },
                label = { Text("Bearer (optional)") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Button(
                onClick = {
                    NinaLog.tap("Setup", "save_bearer")
                    vm.saveBearer(bearer.takeIf { it.isNotBlank() })
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Save token")
            }
            OutlinedTextField(
                pin,
                onValueChange = { pin = it },
                label = { Text("Pairing PIN") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Button(
                onClick = {
                    NinaLog.tap("Setup", "pair_pin")
                    vm.pair(pin) { tok ->
                        showToken = tok
                        scope.launch { snack.showSnackbar("Paired; token stored.") }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Pair")
            }
        }
    }

    Box(Modifier.fillMaxSize()) {
        Column(
            Modifier
                .fillMaxSize()
                .verticalScroll(scrollState)
                .padding(horizontal = 12.dp, vertical = 8.dp)
                .align(Alignment.TopCenter),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Column(
                Modifier
                    .widthIn(max = 720.dp)
                    .fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Card(
                    Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.25f)),
                ) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Connection", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium)
                        Text(
                            "Wi‑Fi shortcuts and live Jetson status.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            OutlinedButton(
                                onClick = {
                                    NinaLog.tap("Setup", "wifi_settings")
                                    ctx.startActivity(
                                        Intent(Settings.ACTION_WIFI_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                                    )
                                    scope.launch {
                                        snack.showSnackbar("Join the Jetson Wi‑Fi, then return and tap Refresh.")
                                    }
                                },
                                modifier = Modifier.weight(1f),
                            ) {
                                Text("Wi‑Fi")
                            }
                            Button(
                                onClick = {
                                    NinaLog.tap("Setup", "refresh_status")
                                    vm.refreshStatus()
                                },
                                modifier = Modifier.weight(1f),
                            ) {
                                Text("Refresh")
                            }
                        }
                        gatewayHint?.let { hint ->
                            Text(
                                hint,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.primary,
                            )
                        }
                    }
                }

                when (state) {
                    CompanionUiState.Loading ->
                        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator()
                        }

                    is CompanionUiState.Error ->
                        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
                            Text(
                                (state as CompanionUiState.Error).text,
                                Modifier.padding(12.dp),
                                color = MaterialTheme.colorScheme.onErrorContainer,
                            )
                        }

                    is CompanionUiState.Ready -> {
                        val ready = state as CompanionUiState.Ready
                        val st = ready.status
                        if (st != null && st.wifiRole == "ap") {
                            Card(
                                Modifier.fillMaxWidth(),
                                colors =
                                    CardDefaults.cardColors(
                                        containerColor = MaterialTheme.colorScheme.primaryContainer,
                                    ),
                            ) {
                                Text(
                                    "On robot access point (Nina Link).",
                                    Modifier.padding(12.dp),
                                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        }
                        if (st != null) {
                            Text(
                                "Daemon: ${ready.url}",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            StatusGridCard(st)
                        }
                    }
                }

                SetupDiagnosticsCard(snack = snack)

                BoxWithConstraints(Modifier.fillMaxWidth()) {
                    val split = maxWidth >= 520.dp
                    if (split) {
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            Card(
                                Modifier.weight(1f),
                                colors = CardDefaults.cardColors(),
                            ) {
                                Column(Modifier.padding(12.dp)) {
                                    WifiBlock()
                                }
                            }
                            Card(
                                Modifier.weight(1f),
                                colors = CardDefaults.cardColors(),
                            ) {
                                Column(Modifier.padding(12.dp)) {
                                    DaemonAuthBlock()
                                }
                            }
                        }
                    } else {
                        Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors()) {
                            Column(Modifier.padding(12.dp)) {
                                WifiBlock()
                            }
                        }
                        Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors()) {
                            Column(Modifier.padding(12.dp)) {
                                DaemonAuthBlock()
                            }
                        }
                    }
                }

                Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Boot mode", fontWeight = FontWeight.Medium, style = MaterialTheme.typography.titleSmall)
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            Button(
                                onClick = {
                                    NinaLog.tap("Setup", "mode", "boot_default")
                                    vm.setMode("boot_default")
                                },
                                modifier = Modifier.weight(1f),
                            ) {
                                Text(
                                    "Default",
                                    style = MaterialTheme.typography.labelLarge,
                                    maxLines = 1,
                                )
                            }
                            OutlinedButton(
                                onClick = {
                                    NinaLog.tap("Setup", "mode", "force_ap")
                                    vm.setMode("force_ap")
                                },
                                modifier = Modifier.weight(1f),
                            ) {
                                Text(
                                    "AP",
                                    style = MaterialTheme.typography.labelLarge,
                                    maxLines = 1,
                                )
                            }
                            OutlinedButton(
                                onClick = {
                                    NinaLog.tap("Setup", "mode", "force_sta")
                                    vm.setMode("force_sta")
                                },
                                modifier = Modifier.weight(1f),
                            ) {
                                Text(
                                    "STA",
                                    style = MaterialTheme.typography.labelLarge,
                                    maxLines = 1,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

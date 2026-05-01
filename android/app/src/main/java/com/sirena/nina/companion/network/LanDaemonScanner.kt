package com.sirena.nina.companion.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.TimeUnit

/**
 * Finds hosts on the same /24 as this tablet that expose [nina-link] on port 8787.
 *
 * Home routers (e.g. 192.168.1.1) often appear as the Wi‑Fi "gateway" but do **not** run the
 * daemon — probing the subnet avoids mistaking the router for the Jetson.
 */
object LanDaemonScanner {

    private val probeClient: OkHttpClient =
        OkHttpClient.Builder()
            .connectTimeout(400, TimeUnit.MILLISECONDS)
            .readTimeout(400, TimeUnit.MILLISECONDS)
            .writeTimeout(400, TimeUnit.MILLISECONDS)
            .callTimeout(450, TimeUnit.MILLISECONDS)
            .build()

    /** Returns sorted `http://host:8787` bases that responded OK on `/health`. */
    suspend fun scanIpv4Subnet(deviceIpv4: String?): List<String> =
        withContext(Dispatchers.IO) {
            if (deviceIpv4.isNullOrBlank()) return@withContext emptyList()
            val parts = deviceIpv4.split(".")
            if (parts.size != 4) return@withContext emptyList()
            val prefix = "${parts[0]}.${parts[1]}.${parts[2]}"
            val ips =
                (1..254).map { host -> "$prefix.$host" }.filter {
                    !it.equals(deviceIpv4, ignoreCase = true)
                }

            val found = ConcurrentLinkedQueue<String>()
            coroutineScope {
                // Chunk to bound concurrency (avoid 254 simultaneous sockets).
                ips.chunked(48).forEach { chunk ->
                    chunk
                        .map { ip ->
                            async(Dispatchers.IO) {
                                val base = "http://$ip:8787"
                                try {
                                    if (probeHealth(base)) {
                                        found.add(base.trimEnd('/'))
                                    }
                                } catch (_: Exception) {
                                }
                            }
                        }.awaitAll()
                }
            }
            found.toSortedSet(String.CASE_INSENSITIVE_ORDER).toList()
        }

    private fun probeHealth(baseUrl: String): Boolean {
        val url = "$baseUrl/health".trimEnd('/')
        val req =
            Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .get()
                .build()
        probeClient.newCall(req).execute().use { resp ->
            return resp.isSuccessful
        }
    }
}

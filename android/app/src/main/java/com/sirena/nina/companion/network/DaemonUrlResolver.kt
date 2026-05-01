package com.sirena.nina.companion.network

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.RouteInfo
import android.os.Build
import java.net.Inet4Address

/**
 * Resolves the Wi‑Fi default gateway (Jetson on Nina AP) and never the tablet's own DHCP address.
 *
 * NetworkManager hotspots typically use **10.42.0.1**; Android tethering often uses **192.168.4.1**.
 */
object DaemonUrlResolver {

    private const val PORT = 8787

    fun gatewayIpv4(context: Context): String? {
        val cm =
            context.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                ?: return null
        val network = cm.activeNetwork ?: return null
        val lp = cm.getLinkProperties(network) ?: return null
        val myIp = deviceIpv4FromLinkProperties(lp)
        return resolveIpv4Gateway(lp, myIp)
    }

    /** This device's IPv4 on the active network (client address). */
    fun deviceIpv4(context: Context): String? {
        val cm =
            context.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                ?: return null
        val network = cm.activeNetwork ?: return null
        val lp = cm.getLinkProperties(network) ?: return null
        return deviceIpv4FromLinkProperties(lp)
    }

    private fun deviceIpv4FromLinkProperties(lp: LinkProperties): String? {
        for (addr in lp.linkAddresses) {
            val ip = addr.address
            if (ip is Inet4Address && !ip.isLoopbackAddress && !ip.isLinkLocalAddress) {
                return ip.hostAddress
            }
        }
        return null
    }

    fun suggestedDaemonBaseUrl(context: Context): String? {
        val gw = gatewayIpv4(context) ?: return null
        return "http://$gw:$PORT"
    }

    /**
     * When route parsing fails or returns nothing usable, map known hotspot client subnets to the usual gateway.
     */
    fun heuristicGatewayForDeviceIp(deviceIpv4: String?): String? {
        if (deviceIpv4.isNullOrBlank()) return null
        return when {
            deviceIpv4.startsWith("10.42.") -> "10.42.0.1"
            deviceIpv4.startsWith("192.168.4.") -> "192.168.4.1"
            else -> null
        }
    }

    fun isLikelyJetsonApGateway(gateway: String?): Boolean {
        if (gateway.isNullOrBlank()) return false
        if (gateway == "192.168.4.1" || gateway == "10.42.0.1") return true
        if (gateway.startsWith("10.42.")) return true
        return false
    }

    /**
     * Prefer the IPv4 default gateway; never return this device's address.
     */
    private fun resolveIpv4Gateway(lp: LinkProperties, deviceIpv4: String?): String? {
        val gateways = mutableListOf<Pair<RouteInfo, String>>()
        for (route in lp.routes) {
            val gateway = route.gateway ?: continue
            if (gateway !is Inet4Address) continue
            val host = gateway.hostAddress ?: continue
            if (deviceIpv4 != null && host.equals(deviceIpv4, ignoreCase = true)) continue
            gateways.add(route to host)
        }

        // 1) Explicit IPv4 default route (API 33+ flag or 0.0.0.0/0)
        for ((route, host) in gateways) {
            if (isIpv4DefaultRoute(route)) return host
        }

        // 2) NM hotspot: client 10.42.x.x → gateway almost always 10.42.0.1
        val hint = heuristicGatewayForDeviceIp(deviceIpv4)
        if (hint != null && gateways.any { it.second == hint }) {
            return hint
        }

        if (gateways.isNotEmpty()) return gateways.first().second

        // 3) Empty / stripped route table — subnet heuristic only
        return hint
    }

    private fun isIpv4DefaultRoute(route: RouteInfo): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (route.isDefaultRoute && route.gateway is Inet4Address) return true
        }
        val dest = route.destination ?: return false
        val ip = dest.address
        return ip is Inet4Address &&
            ip.hostAddress == "0.0.0.0" &&
            dest.prefixLength == 0
    }
}

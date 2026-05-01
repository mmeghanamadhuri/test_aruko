package com.sirena.nina.companion.network

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.os.Build
import java.net.Inet4Address

/**
 * Uses the active network's default gateway so we hit the Jetson (AP) instead of mistyping
 * the tablet's own DHCP address (e.g. 10.42.0.153).
 */
object DaemonUrlResolver {

    private const val PORT = 8787

    fun gatewayIpv4(context: Context): String? {
        val cm =
            context.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                ?: return null
        val network = cm.activeNetwork ?: return null
        val lp = cm.getLinkProperties(network) ?: return null
        return defaultIpv4Gateway(lp)
    }

    /** This device's IPv4 on the active network (client address). */
    fun deviceIpv4(context: Context): String? {
        val cm =
            context.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                ?: return null
        val network = cm.activeNetwork ?: return null
        val lp = cm.getLinkProperties(network) ?: return null
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
     * When the default gateway looks like a typical NM hotspot / shared AP, prefer it for the
     * first connection attempt (Jetson is almost always the gateway on that subnet).
     */
    fun isLikelyJetsonApGateway(gateway: String?): Boolean {
        if (gateway.isNullOrBlank()) return false
        if (gateway == "192.168.4.1" || gateway == "10.42.0.1") return true
        if (gateway.startsWith("10.42.")) return true
        // Some builds use 192.168.x.1 for hotspot links only when we're not on a typical home LAN;
        // conservative: treat 192.168.4.1 already covered.
        return false
    }

    private fun defaultIpv4Gateway(lp: LinkProperties): String? {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val gw = lp.defaultGateway
            if (gw is Inet4Address) return gw.hostAddress
        }
        for (route in lp.routes) {
            val gateway = route.gateway ?: continue
            if (gateway is Inet4Address) return gateway.hostAddress
        }
        return null
    }
}

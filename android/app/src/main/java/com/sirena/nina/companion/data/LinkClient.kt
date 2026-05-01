package com.sirena.nina.companion.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** HTTP client for the Jetson nina-link daemon (matches `nina/link_daemon/api.py`). */
class LinkClient {

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .writeTimeout(45, TimeUnit.SECONDS)
        .build()

    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    suspend fun health(baseUrl: String): JSONObject = withContext(Dispatchers.IO) {
        get("$baseUrl/health")
    }

    suspend fun status(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/status", bearer)
        }

    suspend fun setMode(baseUrl: String, bearer: String?, mode: String): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/mode",
                bearer,
                JSONObject().put("mode", mode).toString(),
            )
        }

    suspend fun saveHomeWifi(
        baseUrl: String,
        bearer: String?,
        ssid: String,
        password: String,
    ): JSONObject = withContext(Dispatchers.IO) {
        post(
            "$baseUrl/v1/wifi/home-credentials",
            bearer,
            JSONObject()
                .put("ssid", ssid)
                .put("password", password)
                .toString(),
        )
    }

    suspend fun connectHome(baseUrl: String, bearer: String?, ssid: String?): JSONObject =
        withContext(Dispatchers.IO) {
            val url = buildString {
                append("$baseUrl/v1/wifi/connect-home")
                if (!ssid.isNullOrBlank()) append("?ssid=${java.net.URLEncoder.encode(ssid, Charsets.UTF_8.name())}")
            }
            post(url, bearer, "{}")
        }

    suspend fun startAp(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/wifi/start-ap", bearer, "{}")
        }

    suspend fun deleteSaved(baseUrl: String, bearer: String?, profileId: String): JSONObject =
        withContext(Dispatchers.IO) {
            delete("$baseUrl/v1/wifi/saved/${java.net.URLEncoder.encode(profileId, Charsets.UTF_8.name())}", bearer)
        }

    suspend fun pair(baseUrl: String, pin: String): JSONObject = withContext(Dispatchers.IO) {
        post(
            "$baseUrl/v1/pair",
            null,
            JSONObject().put("pin", pin).toString(),
        )
    }

    private fun get(url: String, bearer: String? = null): JSONObject {
        val req = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .apply { if (!bearer.isNullOrBlank()) header("Authorization", "Bearer $bearer") }
            .get()
            .build()
        return execute(req)
    }

    private fun post(url: String, bearer: String?, jsonBody: String): JSONObject {
        val body = jsonBody.toRequestBody(jsonMedia)
        val req = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .apply { if (!bearer.isNullOrBlank()) header("Authorization", "Bearer $bearer") }
            .post(body)
            .build()
        return execute(req)
    }

    private fun delete(url: String, bearer: String?): JSONObject {
        val req = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .apply { if (!bearer.isNullOrBlank()) header("Authorization", "Bearer $bearer") }
            .delete()
            .build()
        return execute(req)
    }

    private fun execute(req: Request): JSONObject {
        client.newCall(req).execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                val hint = try {
                    JSONObject(body).toString()
                } catch (_: Exception) {
                    body.ifBlank { resp.message }
                }
                throw LinkApiException(resp.code, hint)
            }
            return if (body.isBlank()) JSONObject() else JSONObject(body)
        }
    }
}

class LinkApiException(val code: Int, message: String) : Exception(message)

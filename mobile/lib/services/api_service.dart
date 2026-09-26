import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:jwt_decoder/jwt_decoder.dart';
import '../config/api_config.dart';

/// Callback invoked when the access token has aged out (or, as a fallback, when
/// a request comes back 401). Should hit the refresh endpoint and update the
/// ApiService's access token, returning true if the new token is ready and the
/// original request can be sent.
typedef RefreshTokenCallback = Future<bool> Function();

/// API response wrapper
class ApiResponse<T> {
  final bool success;
  final T? data;
  final String? message;
  final int statusCode;

  const ApiResponse({
    required this.success,
    this.data,
    this.message,
    required this.statusCode,
  });

  @override
  String toString() =>
      'ApiResponse(success: $success, statusCode: $statusCode, message: $message)';
}

/// HTTP client for BottleCRM API
///
/// Handles authentication headers, organization context,
/// and provides typed request methods.
class ApiService {
  static final ApiService _instance = ApiService._internal();
  factory ApiService() => _instance;
  ApiService._internal();

  http.Client _client = http.Client();

  /// Swap the HTTP client. Tests only: the app shares one client for the
  /// process lifetime, and `ApiService` is a singleton.
  @visibleForTesting
  void setClientForTesting(http.Client client) {
    _client = client;
  }

  // Token and org getters - will be set by AuthService
  String? _accessToken;
  String? _organizationId;

  // Refresh wiring. AuthService registers `refreshAccessToken` here during
  // initialize(); `_refreshInFlight` coalesces concurrent refresh attempts so
  // a burst of expired-token requests only fires one network call to the
  // refresh endpoint.
  RefreshTokenCallback? _refreshCallback;
  Future<bool>? _refreshInFlight;

  /// Set the access token (called by AuthService)
  void setAccessToken(String? token) {
    _accessToken = token;
  }

  /// Set the organization ID (called by AuthService)
  void setOrganizationId(String? orgId) {
    _organizationId = orgId;
  }

  /// Register the refresh-token callback (called once by AuthService.initialize).
  /// Passing null disables auto-refresh.
  void setRefreshCallback(RefreshTokenCallback? callback) {
    _refreshCallback = callback;
  }

  /// Clear authentication state
  void clearAuth() {
    _accessToken = null;
    _organizationId = null;
  }

  /// Run the registered refresh callback at most once per concurrent burst.
  /// Returns true if the access token was refreshed and the caller should
  /// retry the original request.
  Future<bool> _refreshAccessToken() async {
    final cb = _refreshCallback;
    if (cb == null) return false;
    final existing = _refreshInFlight;
    if (existing != null) return existing;
    final fut = cb();
    _refreshInFlight = fut;
    try {
      return await fut;
    } finally {
      _refreshInFlight = null;
    }
  }

  /// How close to expiry counts as expired.
  ///
  /// Covers a token that would age out while the request is in flight, and
  /// small clock differences between the phone and the server.
  static const Duration _expiryGrace = Duration(seconds: 30);

  /// True when the access token is at or near its `exp`.
  ///
  /// A token that will not decode returns false: refreshing cannot repair a
  /// malformed credential, so the request carries it and the server answers.
  bool get _accessTokenNeedsRefresh {
    final token = _accessToken;
    if (token == null) return false;
    try {
      return JwtDecoder.getExpirationDate(
        token,
      ).isBefore(DateTime.now().add(_expiryGrace));
    } catch (_) {
      return false;
    }
  }

  /// Send an HTTP request, refreshing the access token first when it has aged
  /// out. The `send` closure must be safe to invoke twice, every caller below
  /// builds a fresh request inside it.
  Future<http.Response> _sendWithRetry({
    required bool requiresAuth,
    required Future<http.Response> Function(Map<String, String> headers) send,
    Duration? timeout,
  }) async {
    final deadline = timeout ?? ApiConfig.connectTimeout;
    // Refresh BEFORE sending, not after a rejection.
    //
    // This API does not answer 401 for an expired credential. `HasOrgContext`
    // (backend `common/permissions.py`) denies with 403 before DRF's auth layer
    // would emit a 401, and it answers the same way for a missing, malformed or
    // expired token. So the 401 branch below never fires on expiry, and a
    // session that aged out while the app was open stayed broken until the next
    // cold start, where `AuthService.initialize()` does this same check once.
    //
    // The web client refreshes the same way, off the `exp` claim on every
    // request (`frontend/src/hooks.server.js`), rather than off a status code.
    if (requiresAuth && _accessTokenNeedsRefresh && _refreshCallback != null) {
      await _refreshAccessToken();
    }

    var response = await send(
      _buildHeaders(requiresAuth: requiresAuth),
    ).timeout(deadline);

    // Kept as a fallback for a token revoked or rotated out from under us
    // before its `exp`, which the check above cannot see.
    if (requiresAuth &&
        response.statusCode == 401 &&
        _accessToken != null &&
        _refreshCallback != null) {
      final refreshed = await _refreshAccessToken();
      if (refreshed) {
        response = await send(
          _buildHeaders(requiresAuth: requiresAuth),
        ).timeout(deadline);
      }
    }
    return response;
  }

  /// Build request headers
  Map<String, String> _buildHeaders({bool requiresAuth = true}) {
    final headers = Map<String, String>.from(ApiConfig.defaultHeaders);

    if (requiresAuth && _accessToken != null) {
      headers['Authorization'] = 'Bearer $_accessToken';
    }

    if (_organizationId != null) {
      headers['X-Organization-ID'] = _organizationId!;
    }

    return headers;
  }

  /// Parse response body
  dynamic _parseResponse(http.Response response) {
    if (response.body.isEmpty) return null;

    try {
      return jsonDecode(response.body);
    } catch (e) {
      debugPrint('Failed to parse response: ${response.body}');
      return null;
    }
  }

  /// Check if response is successful
  bool _isSuccess(int statusCode) {
    return statusCode >= 200 && statusCode < 300;
  }

  /// Extract error message from response
  /// A sentence a user can act on, instead of the exception's own text.
  ///
  /// `SocketException` and `ClientException` stringify with the host and the
  /// full URI in them, so an offline screen was printing the API hostname at
  /// the user. It is also not information they can use, and every failure that
  /// reaches here has the same answer. A malformed body does not arrive here:
  /// `_parseResponse` swallows that and returns null. The original exception
  /// still goes to `debugPrint` for whoever is debugging.
  String _networkErrorMessage(Object error) =>
      'Cannot reach the server. Check your connection and try again.';

  String _extractErrorMessage(dynamic data, int statusCode) {
    if (data == null) return 'Request failed with status $statusCode';

    if (data is Map<String, dynamic>) {
      // Check common error field names
      if (data.containsKey('detail')) return data['detail'].toString();
      if (data.containsKey('message') && data['message'] is String) {
        return data['message'].toString();
      }

      // Handle backend-shaped errors. The Django views use two flavors:
      //   {"errors": "plain string"}: top-level error message
      //   {"errors": {"field": ["msg", ...]}}: DRF field-level errors
      if (data.containsKey('errors')) {
        final errors = data['errors'];
        if (errors is String && errors.isNotEmpty) return errors;
        if (errors is Map) {
          final fromErrors = _fieldErrorSentences(errors);
          if (fromErrors != null) return fromErrors;
        }
      }

      if (data.containsKey('non_field_errors')) {
        final errors = data['non_field_errors'];
        if (errors is List && errors.isNotEmpty) {
          return errors.first.toString();
        }
      }

      // DRF's own shape, `{"field": ["message"]}`, with no wrapper.
      //
      // That is what a `serializers.ValidationError` raised outside a
      // serializer produces, and it had no branch here: the attachment size
      // limit answered "Files must be 25 MB or smaller." and the phone showed
      // "Request failed with status 400". Last, so the wrapped flavours above
      // still win where a view uses one.
      final fromFields = _fieldErrorSentences(data);
      if (fromFields != null) return fromFields;
    }

    return 'Request failed with status $statusCode';
  }

  /// Turn `{"field_name": ["message"]}` into `Field name: message`, one line
  /// per field. Null when nothing in the map has that shape.
  String? _fieldErrorSentences(Map errors) {
    final sentences = <String>[];
    for (final entry in errors.entries) {
      final label = entry.key.toString().replaceAll('_', ' ');
      final capitalised = label.isEmpty
          ? label
          : '${label[0].toUpperCase()}${label.substring(1)}';
      final messages = entry.value;
      if (messages is List && messages.isNotEmpty) {
        sentences.add('$capitalised: ${messages.first}');
      } else if (messages is String && messages.isNotEmpty) {
        sentences.add('$capitalised: $messages');
      }
    }
    return sentences.isEmpty ? null : sentences.join('\n');
  }

  /// Perform GET request
  Future<ApiResponse<Map<String, dynamic>>> get(
    String url, {
    bool requiresAuth = true,
    Map<String, String>? queryParams,
  }) async {
    try {
      final uri = queryParams != null
          ? Uri.parse(url).replace(queryParameters: queryParams)
          : Uri.parse(url);

      debugPrint('GET $uri');

      final response = await _sendWithRetry(
        requiresAuth: requiresAuth,
        send: (headers) => _client.get(uri, headers: headers),
      );

      final data = _parseResponse(response);
      final success = _isSuccess(response.statusCode);

      return ApiResponse(
        success: success,
        data: success ? data as Map<String, dynamic>? : null,
        message: success
            ? null
            : _extractErrorMessage(data, response.statusCode),
        statusCode: response.statusCode,
      );
    } catch (e) {
      debugPrint('GET error: $e');
      return ApiResponse(
        success: false,
        message: _networkErrorMessage(e),
        statusCode: 0,
      );
    }
  }

  /// Perform an authenticated GET without trying to parse the response as JSON.
  /// Used for private attachments, whose URL must never carry a JWT.
  Future<ApiResponse<Uint8List>> getBytes(
    String url, {
    bool requiresAuth = true,
  }) async {
    try {
      final uri = Uri.parse(url);
      debugPrint('GET (bytes) $uri');
      final response = await _sendWithRetry(
        requiresAuth: requiresAuth,
        send: (headers) => _client.get(uri, headers: headers),
        timeout: ApiConfig.uploadTimeout,
      );
      final success = _isSuccess(response.statusCode);
      final errorData = success ? null : _parseResponse(response);
      return ApiResponse(
        success: success,
        data: success ? response.bodyBytes : null,
        message: success
            ? null
            : _extractErrorMessage(errorData, response.statusCode),
        statusCode: response.statusCode,
      );
    } catch (error) {
      debugPrint('GET (bytes) error: $error');
      return ApiResponse(
        success: false,
        message: _networkErrorMessage(error),
        statusCode: 0,
      );
    }
  }

  /// Perform GET request returning a list
  Future<ApiResponse<List<dynamic>>> getList(
    String url, {
    bool requiresAuth = true,
    Map<String, String>? queryParams,
  }) async {
    try {
      final uri = queryParams != null
          ? Uri.parse(url).replace(queryParameters: queryParams)
          : Uri.parse(url);

      debugPrint('GET (list) $uri');

      final response = await _sendWithRetry(
        requiresAuth: requiresAuth,
        send: (headers) => _client.get(uri, headers: headers),
      );

      final data = _parseResponse(response);
      final success = _isSuccess(response.statusCode);

      return ApiResponse(
        success: success,
        data: success ? (data as List<dynamic>?) : null,
        message: success
            ? null
            : _extractErrorMessage(data, response.statusCode),
        statusCode: response.statusCode,
      );
    } catch (e) {
      debugPrint('GET (list) error: $e');
      return ApiResponse(
        success: false,
        message: _networkErrorMessage(e),
        statusCode: 0,
      );
    }
  }

  /// Perform POST request
  Future<ApiResponse<Map<String, dynamic>>> post(
    String url,
    Map<String, dynamic> body, {
    bool requiresAuth = true,
  }) async {
    try {
      // The URL only. A request body is never safe to log here: the refresh
      // call carries the refresh token, sign-in carries the Google id token,
      // and an ordinary create carries a customer's name, email and phone.
      debugPrint('POST $url');

      final uri = Uri.parse(url);
      final encoded = jsonEncode(body);
      final response = await _sendWithRetry(
        requiresAuth: requiresAuth,
        send: (headers) => _client.post(uri, headers: headers, body: encoded),
      );

      final data = _parseResponse(response);
      final success = _isSuccess(response.statusCode);

      debugPrint('Response status: ${response.statusCode}');
      if (!success) {
        debugPrint('Response body: ${response.body}');
      }

      return ApiResponse(
        success: success,
        // Preserve the parsed body on failure too so callers can read
        // structured error fields (e.g. validation `errors` map, 409 conflict
        // payloads like {running_case_id}). Callers must still check
        // `success` before treating the data as a happy-path payload.
        data: data is Map<String, dynamic> ? data : null,
        message: success
            ? null
            : _extractErrorMessage(data, response.statusCode),
        statusCode: response.statusCode,
      );
    } catch (e) {
      debugPrint('POST error: $e');
      return ApiResponse(
        success: false,
        message: _networkErrorMessage(e),
        statusCode: 0,
      );
    }
  }

  /// POST one file as `multipart/form-data`.
  ///
  /// Separate from [post] rather than a flag on it: that one JSON-encodes a
  /// `Map` body, which is not a shape a file fits into.
  ///
  /// `_buildHeaders` supplies `Content-Type: application/json` and it is left
  /// alone here on purpose. `MultipartRequest.finalize()` overwrites the header
  /// with its own boundary type, and `BaseRequest.headers` compares keys
  /// case-insensitively, so removing it first changes nothing. A test pins the
  /// header that actually goes out, since this is a detail of package:http
  /// rather than a guarantee.
  ///
  /// The request is rebuilt inside the closure because a `MultipartRequest`
  /// streams its body exactly once and cannot be re-sent after a token refresh.
  Future<ApiResponse<Map<String, dynamic>>> postMultipart(
    String url, {
    required String fileField,
    String? filePath,
    // The file's content instead of its path, for a caller that sends one file
    // twice and needs both requests to carry the same bytes (the CSV import
    // previews, then commits). Give exactly one of the two.
    List<int>? fileBytes,
    String? fileName,
    Map<String, String> fields = const {},
    bool requiresAuth = true,
    // PUT or PATCH for an edit that replaces the file. The encoding is the
    // same whatever the verb; which fields go is the caller's decision.
    String method = 'POST',
  }) async {
    assert(
      (filePath == null) != (fileBytes == null),
      'Pass filePath or fileBytes, not both and not neither',
    );
    try {
      // The URL and the field, never the path: a file path on a phone contains
      // the account name, and on iOS the app's container UUID.
      debugPrint('$method (multipart) $url [$fileField]');

      final uri = Uri.parse(url);
      final response = await _sendWithRetry(
        requiresAuth: requiresAuth,
        // A slow connection uploading megabytes needs more than the 30 seconds
        // a JSON call gets.
        timeout: ApiConfig.uploadTimeout,
        send: (headers) async {
          final request = http.MultipartRequest(method, uri);
          request.headers.addAll(headers);
          request.fields.addAll(fields);
          request.files.add(
            fileBytes != null
                ? http.MultipartFile.fromBytes(
                    fileField,
                    fileBytes,
                    filename: fileName,
                  )
                : await http.MultipartFile.fromPath(
                    fileField,
                    filePath!,
                    filename: fileName,
                  ),
          );
          return http.Response.fromStream(await _client.send(request));
        },
      );

      final data = _parseResponse(response);
      final success = _isSuccess(response.statusCode);

      debugPrint('Response status: ${response.statusCode}');

      return ApiResponse(
        success: success,
        data: data is Map<String, dynamic> ? data : null,
        message: success
            ? null
            : _extractErrorMessage(data, response.statusCode),
        statusCode: response.statusCode,
      );
    } catch (e) {
      debugPrint('POST (multipart) error: $e');
      return ApiResponse(
        success: false,
        message: _networkErrorMessage(e),
        statusCode: 0,
      );
    }
  }

  /// Perform PUT request
  Future<ApiResponse<Map<String, dynamic>>> put(
    String url,
    Map<String, dynamic> body, {
    bool requiresAuth = true,
  }) async {
    try {
      debugPrint('PUT $url');

      final uri = Uri.parse(url);
      final encoded = jsonEncode(body);
      final response = await _sendWithRetry(
        requiresAuth: requiresAuth,
        send: (headers) => _client.put(uri, headers: headers, body: encoded),
      );

      final data = _parseResponse(response);
      final success = _isSuccess(response.statusCode);

      return ApiResponse(
        success: success,
        // Preserve the parsed body on failure too. See POST for rationale.
        data: data is Map<String, dynamic> ? data : null,
        message: success
            ? null
            : _extractErrorMessage(data, response.statusCode),
        statusCode: response.statusCode,
      );
    } catch (e) {
      debugPrint('PUT error: $e');
      return ApiResponse(
        success: false,
        message: _networkErrorMessage(e),
        statusCode: 0,
      );
    }
  }

  /// Perform PATCH request
  Future<ApiResponse<Map<String, dynamic>>> patch(
    String url,
    Map<String, dynamic> body, {
    bool requiresAuth = true,
  }) async {
    try {
      debugPrint('PATCH $url');

      final uri = Uri.parse(url);
      final encoded = jsonEncode(body);
      final response = await _sendWithRetry(
        requiresAuth: requiresAuth,
        send: (headers) => _client.patch(uri, headers: headers, body: encoded),
      );

      final data = _parseResponse(response);
      final success = _isSuccess(response.statusCode);

      return ApiResponse(
        success: success,
        // Preserve the parsed body on failure too. See POST for rationale.
        data: data is Map<String, dynamic> ? data : null,
        message: success
            ? null
            : _extractErrorMessage(data, response.statusCode),
        statusCode: response.statusCode,
      );
    } catch (e) {
      debugPrint('PATCH error: $e');
      return ApiResponse(
        success: false,
        message: _networkErrorMessage(e),
        statusCode: 0,
      );
    }
  }

  /// Perform DELETE request
  Future<ApiResponse<Map<String, dynamic>>> delete(
    String url, {
    bool requiresAuth = true,
  }) async {
    try {
      debugPrint('DELETE $url');

      final uri = Uri.parse(url);
      final response = await _sendWithRetry(
        requiresAuth: requiresAuth,
        send: (headers) => _client.delete(uri, headers: headers),
      );

      final data = _parseResponse(response);
      final success = _isSuccess(response.statusCode);

      return ApiResponse(
        success: success,
        // Preserve the parsed body on failure too. See POST for rationale.
        data: data is Map<String, dynamic> ? data : null,
        message: success
            ? null
            : _extractErrorMessage(data, response.statusCode),
        statusCode: response.statusCode,
      );
    } catch (e) {
      debugPrint('DELETE error: $e');
      return ApiResponse(
        success: false,
        message: _networkErrorMessage(e),
        statusCode: 0,
      );
    }
  }
}

local MAX_REQUEST_HEAD_SIZE = 16 * 1024
local INTERNAL_ERROR_BODY = "Internal Server Error\n"
local TOO_LARGE_BODY = "Request Header Fields Too Large\n"

local function send_response(status, body)
    ngx.status = status
    ngx.header.content_type = "text/plain"
    ngx.header.content_length = #body
    return ngx.print(body)
end

local raw_request = ngx.req.raw_header()
local raw_length = type(raw_request) == "string" and #raw_request or 0

if raw_length > MAX_REQUEST_HEAD_SIZE then
    return send_response(431, TOO_LARGE_BODY)
end

local request_line_end = string.find(raw_request or "", "\r\n", 1, true)
local has_terminator = raw_length >= 4 and string.sub(raw_request, -4) == "\r\n\r\n"

if request_line_end == nil or request_line_end == 1 or not has_terminator then
    ngx.log(ngx.ERR, "raw request header was not in the expected HTTP/1.x form")
    return send_response(ngx.HTTP_INTERNAL_SERVER_ERROR, INTERNAL_ERROR_BODY)
end

-- Skip the request line and remove only the empty-line CRLF. The CRLF ending
-- the final header field remains in the response body.
local body = string.sub(raw_request, request_line_end + 2, raw_length - 2)

return send_response(ngx.HTTP_OK, body)

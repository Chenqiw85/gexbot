import { ApiRequestError, isNoLatestRunError } from "@/lib/api";

const emptyLatest = new ApiRequestError("latest", 404, {
  code: "no_latest_run",
  message: "no latest GEX proxy run found for SPY all"
});

if (isNoLatestRunError(emptyLatest)) {
  const code: "no_latest_run" = emptyLatest.code;
  void code;
}

const providerError = new ApiRequestError("latest", 502, {
  code: "provider_error",
  message: "provider failed",
  provider_status_code: 503
});

const providerStatusCode: number | undefined = providerError.providerStatusCode;
const isEmptyState: boolean = isNoLatestRunError(providerError);

void providerStatusCode;
void isEmptyState;

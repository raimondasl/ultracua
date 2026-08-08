# Sample Windows resource state DURING a test run, to a CSV.
#
# WHY THIS EXISTS. `net::ERR_NO_BUFFER_SPACE` (WSAENOBUFS) has now failed CI four times, in four
# different tests, on both shards (R4.22). Four post-mortems have ruled out ports, leaked processes and
# memory — and produced no diagnosis, because the instrument has two holes that no amount of re-reading
# its output can fill:
#
#   1. it runs AFTER the suite exits, so every number is post-teardown. It can prove teardown works;
#      it can never see the PEAK that caused the failure.
#   2. it runs only ON FAILURE, so there is no healthy-run baseline. "total_handles=47262" cannot be
#      called high or normal without one.
#
# So this samples on a timer, throughout, and the workflow runs it on success as well. The point is to
# stop inferring and start comparing.
#
# WHAT IT ADDS THAT THE POST-MORTEM NEVER CAPTURED: **non-paged pool**. WSAENOBUFS is the error Windows
# raises when a socket allocation cannot be satisfied, and non-paged pool is the resource that most
# plausibly runs out — yet it has never once been measured here. Every hypothesis so far (ports, leaks,
# memory, launch churn) has been about something else.
#
# It writes a CSV and nothing else: no thresholds, no verdict, no "looks fine". Judging is for whoever
# reads two runs side by side, and a diagnostic that renders its own opinion is how the last four
# occurrences each got explained away.

param(
    [string]$OutFile = "resource-samples.csv",
    [int]$IntervalSeconds = 5
)

$ErrorActionPreference = "SilentlyContinue"

"ts,elapsed_s,time_wait,handles,processes,chrome_procs,nonpaged_pool_mb,paged_pool_mb,free_mb" |
    Out-File -FilePath $OutFile -Encoding utf8

$start = Get-Date
while ($true) {
    try {
        $now = Get-Date
        $elapsed = [int]($now - $start).TotalSeconds

        $tw = @(Get-NetTCPConnection -State TimeWait -ErrorAction SilentlyContinue).Count
        $procs = @(Get-Process -ErrorAction SilentlyContinue)
        $handles = ($procs | Measure-Object -Property HandleCount -Sum).Sum
        $chrome = @($procs | Where-Object { $_.ProcessName -like 'chrome*' }).Count

        # The measurement this whole file is for.
        $np = (Get-Counter '\Memory\Pool Nonpaged Bytes' -ErrorAction SilentlyContinue).CounterSamples[0].CookedValue
        $pp = (Get-Counter '\Memory\Pool Paged Bytes' -ErrorAction SilentlyContinue).CounterSamples[0].CookedValue

        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
        $free = [int]($os.FreePhysicalMemory / 1KB)

        # `F1`, NOT `N1`. `N1` inserts a thousands separator — `6,434.5` — which splits into two fields
        # and silently corrupts every row of a CSV whose entire purpose is to be compared numerically.
        # Caught by running it once before trusting it; an instrument that lies is worse than none.
        "{0},{1},{2},{3},{4},{5},{6:F1},{7:F1},{8}" -f `
            $now.ToString("HH:mm:ss"), $elapsed, $tw, $handles, $procs.Count, $chrome,
            ($np / 1MB), ($pp / 1MB), $free | Out-File -FilePath $OutFile -Append -Encoding utf8
    } catch {
        # A sampler must never be able to stop a run, or fail one. Skip this tick and carry on.
    }
    Start-Sleep -Seconds $IntervalSeconds
}

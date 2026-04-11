---
name: Bug report
about: Something isn't working as expected
title: "[Bug] "
labels: bug
assignees: ''
---

## Describe the bug

A clear, concise description of what the bug is.

## Steps to reproduce

1. Start the emulator with `docker compose up -d`
2. Send request: `curl ...`
3. Observe: ...

## Expected behavior

What you expected to happen.

## Actual behavior

What actually happened. Include the full response body and HTTP status code if relevant.

## Environment

| Field | Value |
|-------|-------|
| Emulator version | (e.g., `v0.2.0` or Docker image tag) |
| Python version | (e.g., 3.12) |
| OS | (e.g., macOS 14, Ubuntu 22.04) |
| Client | (e.g., `curl`, `Azure.Health.Dicom` SDK, Orthanc) |

## Logs

```
# Paste relevant output from: docker compose logs emulator
```

## Additional context

Any other context about the problem (e.g., DICOM file characteristics, transfer syntax, number of frames).

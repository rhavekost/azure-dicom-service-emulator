# C# Azure.Health.Dicom SDK Integration Guide

Use the official `Azure.Health.Dicom.Client` NuGet package against this emulator the same way you would against a real Azure DICOM Service. The only change is the base URL.

## Prerequisites

- .NET 6+ (or .NET 8 LTS recommended)
- Emulator running: `docker compose up -d` (listens on `http://localhost:8080`)

## Package Installation

```bash
dotnet add package Azure.Health.Dicom.Client
```

Or in your `.csproj`:

```xml
<PackageReference Include="Azure.Health.Dicom.Client" Version="1.*" />
```

## Connecting to the Emulator

The emulator has no authentication. Use an `AzureKeyCredential` with any non-empty string — it is accepted and ignored.

```csharp
using Azure;
using Azure.Health.Dicom.Client;

var baseUrl = new Uri("http://localhost:8080/v2");
var credential = new AzureKeyCredential("dummy");  // emulator ignores auth

DicomWebClient client = new DicomWebClientBuilder()
    .Endpoint(baseUrl)
    .Credential(credential)
    .BuildClient();
```

If you are configuring via `IServiceCollection` (ASP.NET Core / hosted services):

```csharp
builder.Services.AddDicomWebClient(options =>
{
    options.ServiceUri = new Uri(
        Environment.GetEnvironmentVariable("DICOM_SERVICE_URL")
        ?? "http://localhost:8080/v2"
    );
});
```

Set `DICOM_SERVICE_URL=https://<workspace>.dicom.azurehealthcareapis.com/v2` in production and leave it unset (or point it at the emulator) locally.

## Store a DICOM File (STOW-RS)

```csharp
using System.IO;
using Azure.Health.Dicom.Client;

async Task StoreInstanceAsync(DicomWebClient client, string filePath)
{
    await using var fileStream = File.OpenRead(filePath);

    // POST /v2/studies — returns 200 on success, 202 with warnings
    using var response = await client.StoreAsync(new[] { fileStream });

    Console.WriteLine($"Status: {response.GetRawResponse().Status}");

    // Inspect per-instance results
    var dataset = await response.GetValueAsync();
    Console.WriteLine($"Stored: {dataset}");
}
```

To upsert (no duplicate warnings), use `PUT /v2/studies` via the raw HTTP client:

```csharp
using System.Net.Http;

async Task UpsertInstanceAsync(HttpClient http, string filePath)
{
    var boundary = "dicom-boundary";
    var content = new MultipartContent("related", boundary);
    content.Headers.ContentType!.Parameters.Add(
        new System.Net.Http.Headers.NameValueHeaderValue("type", "\"application/dicom\"")
    );

    await using var fileStream = File.OpenRead(filePath);
    var fileContent = new StreamContent(fileStream);
    fileContent.Headers.ContentType = new("application/dicom");
    content.Add(fileContent);

    var response = await http.PutAsync("http://localhost:8080/v2/studies", content);
    response.EnsureSuccessStatusCode();
}
```

## Search Studies (QIDO-RS)

```csharp
async Task SearchStudiesAsync(DicomWebClient client)
{
    // All studies
    var allStudies = await client.QueryStudiesAsync();
    await foreach (var dataset in allStudies.Value)
    {
        Console.WriteLine(dataset.GetString(DicomTag.StudyInstanceUID));
    }

    // Filter by PatientID
    var filtered = await client.QueryStudiesAsync("PatientID=TEST-001");
    await foreach (var dataset in filtered.Value)
    {
        Console.WriteLine($"Patient: {dataset.GetString(DicomTag.PatientName)}");
    }

    // Wildcard — emulator supports * and ? wildcards
    var wildcard = await client.QueryStudiesAsync("PatientID=PAT*");

    // Fuzzy name matching
    var fuzzy = await client.QueryStudiesAsync(
        "PatientName=smith&fuzzymatching=true"
    );
}
```

## Retrieve Metadata (WADO-RS)

```csharp
async Task GetStudyMetadataAsync(DicomWebClient client, string studyInstanceUid)
{
    var response = await client.RetrieveStudyMetadataAsync(studyInstanceUid);
    await foreach (var dataset in response.Value)
    {
        Console.WriteLine($"Study UID : {dataset.GetString(DicomTag.StudyInstanceUID)}");
        Console.WriteLine($"Study Date: {dataset.GetString(DicomTag.StudyDate)}");
        Console.WriteLine($"Patient   : {dataset.GetString(DicomTag.PatientName)}");
    }
}

async Task GetInstanceAsync(
    DicomWebClient client,
    string studyUid,
    string seriesUid,
    string instanceUid)
{
    var response = await client.RetrieveInstanceAsync(studyUid, seriesUid, instanceUid);
    await using var stream = await response.Value.Content.ReadAsStreamAsync();
    await using var file = File.Create($"{instanceUid}.dcm");
    await stream.CopyToAsync(file);
    Console.WriteLine($"Saved instance to {instanceUid}.dcm");
}
```

## Delete a Study

```csharp
async Task DeleteStudyAsync(DicomWebClient client, string studyInstanceUid)
{
    // Returns 204 No Content on success
    await client.DeleteStudyAsync(studyInstanceUid);
    Console.WriteLine($"Deleted study {studyInstanceUid}");
}

async Task DeleteSeriesAsync(
    DicomWebClient client,
    string studyUid,
    string seriesUid)
{
    await client.DeleteSeriesAsync(studyUid, seriesUid);
}

async Task DeleteInstanceAsync(
    DicomWebClient client,
    string studyUid,
    string seriesUid,
    string instanceUid)
{
    await client.DeleteInstanceAsync(studyUid, seriesUid, instanceUid);
}
```

## Change Feed (Azure-Specific)

```csharp
using System.Net.Http.Json;

async Task PollChangeFeedAsync(HttpClient http)
{
    var entries = await http.GetFromJsonAsync<List<ChangeFeedEntry>>(
        "http://localhost:8080/v2/changefeed"
    );
    foreach (var entry in entries ?? [])
    {
        Console.WriteLine($"Seq={entry.Sequence} Action={entry.Action}");
    }
}

record ChangeFeedEntry(long Sequence, string Action, string State);
```

## Using the Emulator in Tests

### xUnit / NUnit

```csharp
using Xunit;
using Azure;
using Azure.Health.Dicom.Client;

public class DicomIntegrationTests : IAsyncLifetime
{
    private DicomWebClient _client = null!;

    public Task InitializeAsync()
    {
        var url = Environment.GetEnvironmentVariable("DICOM_SERVICE_URL")
                  ?? "http://localhost:8080/v2";

        _client = new DicomWebClientBuilder()
            .Endpoint(new Uri(url))
            .Credential(new AzureKeyCredential("dummy"))
            .BuildClient();

        return Task.CompletedTask;
    }

    public Task DisposeAsync() => Task.CompletedTask;

    [Fact]
    public async Task StoreAndRetrieve_RoundTrip_Succeeds()
    {
        // Arrange — load a test .dcm file from your test fixtures folder
        await using var stream = File.OpenRead("TestData/ct-sample.dcm");

        // Act — store
        using var storeResponse = await _client.StoreAsync(new[] { stream });
        Assert.True(
            storeResponse.GetRawResponse().Status is 200 or 202,
            $"Store failed: {storeResponse.GetRawResponse().Status}"
        );

        // Act — search
        var studies = await _client.QueryStudiesAsync();
        var list = new List<object>();
        await foreach (var ds in studies.Value) list.Add(ds);

        // Assert
        Assert.NotEmpty(list);
    }
}
```

### Environment Variable Configuration

Set the target URL in your CI environment to switch between the emulator and a real service:

| Environment | `DICOM_SERVICE_URL` value |
|-------------|--------------------------|
| Local dev   | `http://localhost:8080/v2` |
| CI (Docker) | `http://dicom-emulator:8080/v2` |
| Staging     | `https://<workspace>.dicom.azurehealthcareapis.com/v2` |
| Production  | `https://<workspace>.dicom.azurehealthcareapis.com/v2` |

In GitHub Actions, add the emulator as a service container:

```yaml
services:
  postgres:
    image: postgres:15
    env:
      POSTGRES_USER: emulator
      POSTGRES_PASSWORD: emulator
      POSTGRES_DB: dicom_emulator

  dicom-emulator:
    image: rhavekost/azure-dicom-service-emulator:latest
    ports:
      - 8080:8080
    env:
      DATABASE_URL: postgresql+asyncpg://emulator:emulator@postgres:5432/dicom_emulator
    options: >-
      --health-cmd "curl -f http://localhost:8080/health"
      --health-interval 5s
      --health-retries 10

steps:
  - name: Run tests
    env:
      DICOM_SERVICE_URL: http://localhost:8080/v2
    run: dotnet test
```

## Authentication Notes

The real Azure DICOM Service requires a bearer token from Azure AD. The emulator accepts any value (or no value) in the `Authorization` header. When writing code that must work with both:

```csharp
// In production, obtain a real token:
// var credential = new DefaultAzureCredential();
// In local dev / CI, use a dummy key:
var credential = new AzureKeyCredential(
    Environment.GetEnvironmentVariable("DICOM_API_KEY") ?? "dummy"
);
```

A future emulator release will add an `--auth mock` flag that accepts any well-formed bearer token, making it easier to test auth flows end-to-end without a live Azure AD tenant.

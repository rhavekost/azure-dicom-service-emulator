# Storage Path Mismatch Investigation

**Investigation Date:** 2026-02-17
**Status:** CRITICAL BUG IDENTIFIED
**Task:** Task 10 - Comprehensive Bugfix Plan

## Executive Summary

A **critical path format mismatch** exists between DICOM file storage (`dicom_engine.py`) and frame retrieval (`frame_cache.py`, rendered endpoint handlers). This bug prevents frame-level WADO-RS operations from working correctly.

## Path Format Comparison

### Storage (dicom_engine.py)

**Location:** `/Users/rhavekost/Projects/rhavekost/azure-dicom-service-emulator/app/services/dicom_engine.py`
**Lines:** 178-201

```python
def store_instance(data: bytes, ds: Dataset) -> str:
    """Store a DICOM instance to the filesystem. Returns the file path."""
    ensure_storage_dir()

    study_uid = str(ds.StudyInstanceUID)
    series_uid = str(ds.SeriesInstanceUID)
    sop_uid = str(ds.SOPInstanceUID)

    # Validate UIDs to prevent directory traversal attacks
    _validate_uid(study_uid)
    _validate_uid(series_uid)
    _validate_uid(sop_uid)

    study_dir = os.path.join(STORAGE_DIR, study_uid, series_uid)
    os.makedirs(study_dir, exist_ok=True)

    file_path = os.path.join(study_dir, f"{sop_uid}.dcm")
    with open(file_path, "wb") as f:
        f.write(data)

    return file_path
```

**Stored Path Format:**
```
/data/dicom/{study_uid}/{series_uid}/{sop_uid}.dcm
```

**Example:**
```
/data/dicom/1.2.3.4/5.6.7.8/9.10.11.12.dcm
```

### Retrieval (frame_cache.py)

**Location:** `/Users/rhavekost/Projects/rhavekost/azure-dicom-service-emulator/app/services/frame_cache.py`
**Lines:** 53-59

```python
def get_or_extract(self, study_uid: str, series_uid: str, instance_uid: str) -> list[Path]:
    """Get frames from cache or extract if not cached."""
    # Check if previously failed
    if self._is_failed(instance_uid):
        raise ValueError(f"Instance {instance_uid} failed extraction in last hour")

    # Build paths
    instance_dir = self.storage_dir / study_uid / series_uid / instance_uid
    dcm_path = instance_dir / "instance.dcm"
    frames_dir = instance_dir / "frames"

    if not dcm_path.exists():
        raise FileNotFoundError(f"Instance not found: {instance_uid}")
```

**Expected Path Format:**
```
/data/dicom/{study_uid}/{series_uid}/{instance_uid}/instance.dcm
```

**Example:**
```
/data/dicom/1.2.3.4/5.6.7.8/9.10.11.12/instance.dcm
```

### Rendered Image Endpoints (dicomweb.py)

**Location:** `/Users/rhavekost/Projects/rhavekost/azure-dicom-service-emulator/app/routers/dicomweb.py`
**Lines:** 717, 789

Both rendered image endpoints use the same path format as `frame_cache.py`:

```python
# Line 717 (rendered instance endpoint)
dcm_path = DICOM_STORAGE_DIR / study_uid / series_uid / instance_uid / "instance.dcm"

# Line 789 (rendered frame endpoint)
dcm_path = DICOM_STORAGE_DIR / study_uid / series_uid / instance_uid / "instance.dcm"
```

## The Problem

### Storage vs. Retrieval Mismatch

| Component | Path Format | Example |
|-----------|------------|---------|
| **Storage** (`dicom_engine.py`) | `{storage}/{study}/{series}/{sop}.dcm` | `/data/dicom/1.2.3/5.6.7/9.10.11.dcm` |
| **Retrieval** (`frame_cache.py`) | `{storage}/{study}/{series}/{sop}/instance.dcm` | `/data/dicom/1.2.3/5.6.7/9.10.11/instance.dcm` |
| **Rendered** (`dicomweb.py`) | `{storage}/{study}/{series}/{sop}/instance.dcm` | `/data/dicom/1.2.3/5.6.7/9.10.11/instance.dcm` |

### Impact

1. **STOW-RS (POST /v2/studies)** stores files at: `{sop_uid}.dcm`
2. **Frame retrieval (GET /v2/studies/{study}/series/{series}/instances/{instance}/frames/{frame})** looks for: `{sop_uid}/instance.dcm`
3. **Result:** Frame retrieval always fails with `FileNotFoundError`

### Affected Endpoints

All frame-level and rendered WADO-RS endpoints are broken:

1. **Frame Retrieval:**
   - `GET /v2/studies/{study}/series/{series}/instances/{instance}/frames/{frame}` (line 623)
   - `GET /v2/studies/{study}/series/{series}/instances/{instance}/frames` (line 647)

2. **Rendered Images:**
   - `GET /v2/studies/{study}/series/{series}/instances/{instance}/rendered` (line 717)
   - `GET /v2/studies/{study}/series/{series}/instances/{instance}/frames/{frame}/rendered` (line 789)

### Why This Wasn't Caught

1. **Basic STOW/WADO operations work:** Metadata retrieval uses the database, not the filesystem directly
2. **Full instance retrieval uses database `file_path`:** The `read_instance()` function reads from the path stored in the database
3. **Frame operations are less commonly tested:** Frame-level operations require multi-frame DICOM files

## Database Storage

The `DicomInstance.file_path` column stores the path returned by `store_instance()`:

**Path stored in DB:**
```
/data/dicom/{study_uid}/{series_uid}/{sop_uid}.dcm
```

This is used by:
- `read_instance()` in `dicom_engine.py` (line 204-207)
- Full instance WADO-RS retrieval
- DELETE operations

## Root Cause Analysis

### How This Happened

Looking at the codebase evolution:

1. **Original implementation** (`dicom_engine.py`): Used flat structure `{study}/{series}/{sop}.dcm`
2. **Phase 2 frame support** (`frame_cache.py`): Introduced subdirectory structure for frame extraction:
   ```
   {study}/{series}/{sop}/
       instance.dcm
       frames/
           frame_0001.raw
           frame_0002.raw
   ```
3. **Disconnect:** Frame extraction code assumed storage would use the subdirectory structure, but `store_instance()` was never updated

### Supporting Evidence

From test files:
- `tests/unit/services/test_frame_cache.py` (lines 21, 57, 86, 111): All tests manually create `instance.dcm` in subdirectories
- `tests/integration/test_wado_rs_frames.py` (line 84): Integration tests copy files into subdirectory format
- `tests/integration/test_wado_rs_accept_validation.py` (line 110): Same pattern

**Tests work around the bug** by manually restructuring files before testing frame operations.

## Proposed Fix

### Option 1: Update Storage to Use Subdirectories (RECOMMENDED)

**Change:** Modify `dicom_engine.store_instance()` to store files at `{study}/{series}/{sop}/instance.dcm`

**Pros:**
- Aligns with frame cache design
- Cleaner organization (each instance has its own directory)
- Better supports future enhancements (thumbnails, alternate representations)
- Matches Azure DICOM Service conceptual model

**Cons:**
- Breaks existing storage (migration needed)
- Database `file_path` values need updating

**Implementation:**

```python
def store_instance(data: bytes, ds: Dataset) -> str:
    """Store a DICOM instance to the filesystem. Returns the file path."""
    ensure_storage_dir()

    study_uid = str(ds.StudyInstanceUID)
    series_uid = str(ds.SeriesInstanceUID)
    sop_uid = str(ds.SOPInstanceUID)

    # Validate UIDs to prevent directory traversal attacks
    _validate_uid(study_uid)
    _validate_uid(series_uid)
    _validate_uid(sop_uid)

    # Create instance directory: {storage}/{study}/{series}/{sop}/
    instance_dir = os.path.join(STORAGE_DIR, study_uid, series_uid, sop_uid)
    os.makedirs(instance_dir, exist_ok=True)

    # Store as instance.dcm
    file_path = os.path.join(instance_dir, "instance.dcm")
    with open(file_path, "wb") as f:
        f.write(data)

    return file_path
```

### Option 2: Update Retrieval to Match Storage

**Change:** Modify `frame_cache.py` and rendered endpoints to expect `{study}/{series}/{sop}.dcm`

**Pros:**
- No migration needed
- Simpler directory structure

**Cons:**
- Frame extraction writes to same directory as source files
- Less organized (frame cache directory alongside DICOM files)
- Doesn't follow principle of "one directory per instance"
- Harder to support future enhancements

**Not recommended** because it goes against the design intent of having a dedicated directory per instance.

### Option 3: Hybrid Approach

Store at `{sop}.dcm` but symlink or copy to `{sop}/instance.dcm` during frame extraction.

**Not recommended:** Adds complexity without clear benefits.

## Recommendation

**Implement Option 1**: Update storage to use instance subdirectories.

### Migration Path

1. **Update `store_instance()`** as shown above
2. **Add migration utility** to restructure existing files:
   ```python
   # pseudocode
   for each file at {study}/{series}/{sop}.dcm:
       create directory {study}/{series}/{sop}/
       move {study}/{series}/{sop}.dcm to {study}/{series}/{sop}/instance.dcm
       update database: file_path = new_path
   ```
3. **Update documentation** to reflect new structure
4. **Verify all tests pass** (they should, since tests already use subdirectory structure)

### Breaking Change Considerations

Since this is an emulator in active development (not released v1.0 yet), this is acceptable:
- Document as breaking change in CHANGELOG
- Provide migration script
- Note in README that existing deployments need to run migration

For production systems, consider:
- Version the storage format
- Support both formats during transition period
- Automatic migration on startup (with backup)

## Files Requiring Changes

### Core Implementation

1. **`app/services/dicom_engine.py`**
   - `store_instance()` function (lines 178-201)
   - Update to create subdirectory and store as `instance.dcm`

### Database Migration

2. **Migration script (new file)**
   - `scripts/migrate_storage_to_subdirs.py` or similar
   - Restructure existing files
   - Update `DicomInstance.file_path` in database

### Documentation

3. **`README.md`**
   - Update "Storage Structure" section (if exists)
   - Document new path format

4. **`CLAUDE.md`**
   - Update "Project Structure" diagram
   - Note breaking change in v2

### Tests (Should already pass after fix)

No test changes needed - tests already use subdirectory structure:
- `tests/unit/services/test_frame_cache.py`
- `tests/integration/test_wado_rs_frames.py`
- `tests/integration/test_wado_rs_accept_validation.py`

## Verification Steps

After implementing fix:

1. **Store a DICOM file:**
   ```bash
   curl -X POST http://localhost:8080/v2/studies \
     -H "Content-Type: multipart/related; type=application/dicom" \
     --data-binary @test.dcm
   ```

2. **Verify storage structure:**
   ```bash
   ls -R /data/dicom/
   # Should show: {study}/{series}/{sop}/instance.dcm
   ```

3. **Test frame retrieval:**
   ```bash
   curl http://localhost:8080/v2/studies/{study}/series/{series}/instances/{instance}/frames/1
   # Should return frame data, not 404
   ```

4. **Test rendered endpoint:**
   ```bash
   curl http://localhost:8080/v2/studies/{study}/series/{series}/instances/{instance}/rendered
   # Should return JPEG/PNG, not 404
   ```

5. **Run full test suite:**
   ```bash
   pytest tests/
   ```

## Related Issues

This investigation relates to:
- Frame retrieval failures reported in testing
- "Instance not found" errors on frame endpoints
- Test workarounds that manually restructure files

## Timeline

- **Discovered:** 2026-02-17 (Task 10 investigation)
- **Severity:** Critical (blocks frame-level WADO-RS functionality)
- **Recommended Fix Priority:** Immediate (before v1.0 release)

## Conclusion

This is **not a documentation issue** - it is a **critical bug** that requires code changes to align storage and retrieval path formats. The recommended fix is to update `dicom_engine.store_instance()` to use instance subdirectories, which aligns with the frame extraction design and provides better organization for future enhancements.

The bug's impact is significant but contained to frame-level and rendered image operations. Basic STOW-RS and metadata retrieval operations work correctly because they use the database-stored path for full instance retrieval.

**Action Required:** Implement Option 1 fix in Task 11 (or as immediate hotfix if frame operations are needed before completing bugfix plan).

import os

def create_super():
    super_size = 3758096384  # 3.5GB
    system_img = "/mnt/t7_storage/QIN/system_modified.img"
    vendor_img = "/mnt/t7_storage/QIN/original_extracted/vendor_a.img"
    product_img = "/mnt/t7_storage/QIN/original_extracted/product_a.img"
    output_bin = "/mnt/t7_storage/QIN/super_modified.bin"

    print(f"Creating {output_bin}...")
    
    with open(output_bin, "wb") as out:
        # 1. Start with the original super header (first 1MB)
        with open("/mnt/t7_storage/QIN/qin_original/super.bin", "rb") as orig:
            out.write(orig.read(1024 * 1024))
        
        # 2. Seek to where system_a should be (this is the risky part)
        # Instead of guessing, we will just flash the system_modified.img
        # directly to the start of the super partition for testing.
        # WAIT - This is too risky. 
        
    print("Script finished.")

if __name__ == "__main__":
    create_super()
#!/usr/bin/env python3
"""
Build a super.bin image for MediaTek Android devices using the liblp format.
Reads system_modified.img, vendor_a.img, product_a.img from the current directory.
Outputs super_modified.bin.
"""

import struct
import zlib
import os

# Constants from the Android liblp specification
LP_MAGIC = 0x414C5030          # "LP0\x00" (little‑endian)
LP_MAJOR_VERSION = 10
LP_MINOR_VERSION = 0
LP_HEADER_SIZE = 4096          # one block
LP_PARTITION_ENTRY_SIZE = 128  # bytes per partition entry
BLOCK_SIZE = 4096
METADATA_SLOT_COUNT = 2
METADATA_MAX_SIZE = 65536      # bytes (as given in the spec)
TOTAL_SUPER_SIZE = 3758096384  # bytes

# Partition definitions (order must match the device's partition table)
PARTITIONS = [
    ("system_a", "system_modified.img", 1635680256),
    ("vendor_a", "vendor_a.img", 327831552),
    ("product_a", "product_a.img", 165457920),
]

# Group name (used in the partition table attribute)
GROUP_NAME = "qin_dynamic_partitions"


def crc32(data: bytes) -> int:
    """Return CRC‑32 of *data* (unsigned 32‑bit)."""
    return zlib.crc32(data) & 0xFFFFFFFF


def build_super():
    # 1. Read all partition images
    images = []
    for name, filename, expected_size in PARTITIONS:
        if not os.path.isfile(filename):
            raise FileNotFoundError(f"Missing partition image: {filename}")
        with open(filename, "rb") as f:
            data = f.read()
        if len(data) != expected_size:
            raise ValueError(
                f"Size mismatch for {name}: expected {expected_size}, got {len(data)}"
            )
        images.append(data)

    # 2. Build the partition table (array of lp_partition entries)
    #    Each entry is 128 bytes, defined by struct lp_partition in liblp.
    #    Fields (little‑endian):
    #      - name (36 bytes, null‑terminated)
    #      - attributes (4 bytes)
    #      - first_extent_index (4 bytes)
    #      - num_extents (4 bytes)
    #      - reserved (80 bytes)
    #    We use a single extent per partition (linear mapping).
    partition_table = b""
    current_sector = 0  # in 512‑byte sectors (starting after header+table)
    # The header and table occupy the first block (4096 bytes) = 8 sectors
    header_sectors = LP_HEADER_SIZE // 512  # 8
    # The partition table occupies the next block (4096 bytes) = 8 sectors
    table_sectors = LP_PARTITION_ENTRY_SIZE * len(PARTITIONS) // 512
    # Align table to block boundary (already 4096)
    # The first data sector is after header + table
    data_start_sector = header_sectors + table_sectors  # 8 + (3*128)/512 = 8 + 0.75 = 8.75? Actually 3*128=384 bytes < 512, so table_sectors = 1 (rounded up). We'll align to block size.
    # Simpler: we place the partition table at offset 4096 (after header), and the first data at offset 8192 (two blocks).
    # So data_start_sector = 8192 // 512 = 16.
    data_start_sector = 16

    for idx, (name, filename, size) in enumerate(PARTITIONS):
        # name (36 bytes, null‑terminated)
        name_bytes = name.encode("ascii") + b"\x00" * (36 - len(name))
        # attributes: bit 0 = readonly? We'll set 0 for now.
        attributes = 0
        # first_extent_index: 0 (we use a single extent)
        first_extent_index = 0
        # num_extents: 1
        num_extents = 1
        # reserved (80 bytes)
        reserved = b"\x00" * 80

        entry = struct.pack(
            "<36sIII80s",
            name_bytes,
            attributes,
            first_extent_index,
            num_extents,
            reserved,
        )
        partition_table += entry

    # Pad partition table to a full block (4096 bytes)
    partition_table += b"\x00" * (BLOCK_SIZE - len(partition_table))

    # 3. Build the super header (struct lp_super_partition)
    #    Fields (little‑endian):
    #      - magic (4 bytes)
    #      - major_version (2 bytes)
    #      - minor_version (2 bytes)
    #      - header_size (2 bytes)
    #      - header_crc32 (4 bytes) – set to 0 for now, compute later
    #      - partition_count (4 bytes)
    #      - block_size (4 bytes)
    #      - total_sectors (8 bytes) – in 512‑byte sectors
    #      - metadata_slot_count (4 bytes)
    #      - metadata_max_size (4 bytes)
    #      - reserved (12 bytes)
    #      - partition_table_offset (4 bytes) – offset in bytes from start of header
    #      - partition_table_size (4 bytes) – size in bytes of the table
    #      - partition_table_crc32 (4 bytes) – CRC of the table
    #      - reserved2 (4 bytes)
    #      - geometry (??) – we'll skip for simplicity, fill with zeros
    #    The total header size is 4096 bytes.

    total_sectors = TOTAL_SUPER_SIZE // 512  # 7340032
    partition_count = len(PARTITIONS)
    partition_table_offset = LP_HEADER_SIZE  # 4096
    partition_table_size = len(partition_table)  # 4096

    # Compute CRC of the partition table
    partition_table_crc = crc32(partition_table)

    # Build header without CRC (set header_crc32 to 0)
    header_without_crc = struct.pack(
        "<4sHHHIIQII12sIII4s",
        struct.pack("<I", LP_MAGIC),
        LP_MAJOR_VERSION,
        LP_MINOR_VERSION,
        LP_HEADER_SIZE,
        0,  # header_crc32 placeholder
        partition_count,
        BLOCK_SIZE,
        total_sectors,
        METADATA_SLOT_COUNT,
        METADATA_MAX_SIZE,
        b"\x00" * 12,  # reserved
        partition_table_offset,
        partition_table_size,
        partition_table_crc,
        0,  # reserved2
        b"\x00" * 4,  # geometry placeholder
    )
    # Pad header to 4096 bytes
    header_without_crc += b"\x00" * (LP_HEADER_SIZE - len(header_without_crc))

    # Compute CRC of the header (excluding the CRC field itself)
    # The CRC field is at offset 8 (bytes 8‑11). We'll compute CRC over the whole header
    # with that field set to zero, then replace it.
    header_crc = crc32(header_without_crc)

    # Now build the final header with the correct CRC
    header = bytearray(header_without_crc)
    struct.pack_into("<I", header, 8, header_crc)
    header = bytes(header)

    # 4. Write the output file
    output_path = "super_modified.bin"
    with open(output_path, "wb") as f:
        # Write header (4096 bytes)
        f.write(header)
        # Write partition table (4096 bytes)
        f.write(partition_table)
        # Write each partition image, aligned to block size
        for data in images:
            f.write(data)
            # Pad to block boundary (already aligned because sizes are multiples of 4096)
            # But ensure we pad to next block if needed (sizes are multiples of 4096)
            remainder = len(data) % BLOCK_SIZE
            if remainder:
                f.write(b"\x00" * (BLOCK_SIZE - remainder))
        # Pad remaining space to TOTAL_SUPER_SIZE
        current_pos = f.tell()
        if current_pos > TOTAL_SUPER_SIZE:
            raise RuntimeError(
                f"Data exceeds super size: {current_pos} > {TOTAL_SUPER_SIZE}"
            )
        remaining = TOTAL_SUPER_SIZE - current_pos
        if remaining > 0:
            f.write(b"\x00" * remaining)

    print(f"Successfully created {output_path} ({TOTAL_SUPER_SIZE} bytes)")


if __name__ == "__main__":
    build_super()

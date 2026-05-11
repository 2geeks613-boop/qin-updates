import struct
import zlib
import os

# Constants from the Android liblp specification
LP_MAGIC = 0x414C5030          
LP_MAJOR_VERSION = 10
LP_MINOR_VERSION = 0
LP_HEADER_SIZE = 4096          
LP_PARTITION_ENTRY_SIZE = 128  
BLOCK_SIZE = 4096
METADATA_SLOT_COUNT = 2
METADATA_MAX_SIZE = 65536      
TOTAL_SUPER_SIZE = 3758096384  

# Updated absolute paths
PARTITIONS = [
    ("system_a", "/mnt/t7_storage/QIN/system_modified.img", 1635680256),
    ("vendor_a", "/mnt/t7_storage/QIN/original_extracted/vendor_a.img", 327831552),
    ("product_a", "/mnt/t7_storage/QIN/original_extracted/product_a.img", 165457920),
]

def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF

def build_super():
    images = []
    for name, filename, expected_size in PARTITIONS:
        if not os.path.isfile(filename):
            raise FileNotFoundError(f"Missing partition image: {filename}")
        with open(filename, "rb") as f:
            data = f.read()
        if len(data) != expected_size:
            raise ValueError(f"Size mismatch for {name}: expected {expected_size}, got {len(data)}")
        images.append(data)

    # Build partition table (array of lp_partition entries)
    partition_table = b""
    for idx, (name, filename, size) in enumerate(PARTITIONS):
        name_bytes = name.encode("ascii") + b"\x00" * (36 - len(name))
        attributes = 0
        first_extent_index = idx  # index into the extents array (written later)
        num_extents = 1
        reserved = b"\x00" * 80
        entry = struct.pack("<36sIII80s", name_bytes, attributes, first_extent_index, num_extents, reserved)
        partition_table += entry

    # Pad partition table to a full block (4096 bytes)
    partition_table += b"\x00" * (BLOCK_SIZE - len(partition_table))

    # Build extents array (one lp_extent per partition)
    # Each extent: num_sectors (8 bytes), target_type (4 bytes), target_data (4 bytes)
    # target_type = 0 for linear mapping
    # target_data = starting sector (in 512‑byte sectors) relative to super partition start
    # We'll compute the starting sector after header + table + extents + padding
    extents = b""
    # The data will start after header (4096) + table (4096) + extents (3*16=48) + padding to block boundary
    # Compute the offset where the first partition's data will begin
    data_offset = LP_HEADER_SIZE + len(partition_table) + 3 * 16  # 4096+4096+48 = 8240
    # Align to block size
    if data_offset % BLOCK_SIZE != 0:
        data_offset += BLOCK_SIZE - (data_offset % BLOCK_SIZE)
    data_start_sector = data_offset // 512  # in 512‑byte sectors

    for idx, (name, filename, size) in enumerate(PARTITIONS):
        num_sectors = size // 512
        target_type = 0
        target_data = data_start_sector  # starting sector for this partition
        extents += struct.pack("<QII", num_sectors, target_type, target_data)
        # Advance data_start_sector for the next partition
        data_start_sector += num_sectors

    # Pad extents to block boundary (so that image data starts at a block boundary)
    extents_padding = (BLOCK_SIZE - (len(extents) % BLOCK_SIZE)) % BLOCK_SIZE
    extents += b"\x00" * extents_padding

    total_sectors = TOTAL_SUPER_SIZE // 512
    partition_count = len(PARTITIONS)
    partition_table_offset = LP_HEADER_SIZE
    partition_table_size = len(partition_table)
    partition_table_crc = crc32(partition_table)

    # Build super header (struct lp_super_partition)
    # Format: 4s (magic), H (major), H (minor), H (header_size), I (crc), I (part_count), I (block_size), Q (sectors), I (slots), I (max_size), 12s (res), I (table_off), I (table_size), I (table_crc), I (res2), 4s (geom)
    header_without_crc = struct.pack(
        "<4sHHHIIIQII12sIIII4s",
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
    header_without_crc += b"\x00" * (LP_HEADER_SIZE - len(header_without_crc))
    header_crc = crc32(header_without_crc)
    header = bytearray(header_without_crc)
    struct.pack_into("<I", header, 8, header_crc)
    header = bytes(header)

    output_path = "/mnt/t7_storage/QIN/super_modified.bin"
    with open(output_path, "wb") as f:
        f.write(header)
        f.write(partition_table)
        f.write(extents)
        for data in images:
            f.write(data)
            remainder = len(data) % BLOCK_SIZE
            if remainder:
                f.write(b"\x00" * (BLOCK_SIZE - remainder))
        current_pos = f.tell()
        remaining = TOTAL_SUPER_SIZE - current_pos
        if remaining > 0:
            f.write(b"\x00" * remaining)
    print(f"Successfully created {output_path}")

if __name__ == "__main__":
    build_super()
    # To test the output image, run:
    #   lpdump /mnt/t7_storage/QIN/super_modified.bin
    # or check the magic bytes:
    #   xxd /mnt/t7_storage/QIN/super_modified.bin | head -1

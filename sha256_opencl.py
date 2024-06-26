import time

import pyopencl as cl
import numpy as np

# Código del kernel OpenCL corregido
kernel_code = """
#define uchar unsigned char
#define uint unsigned int
#define ulong unsigned long

__constant uint k[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
};

// Rotación a la derecha
uint rotr(uint x, uint n) {
    return (x >> n) | (x << (32 - n));
}

// Funciones auxiliares SHA-256
uint ch(uint x, uint y, uint z) {
    return (x & y) ^ (~x & z);
}

uint maj(uint x, uint y, uint z) {
    return (x & y) ^ (x & z) ^ (y & z);
}

uint sigma0(uint x) {
    return rotr(x, 2) ^ rotr(x, 13) ^ rotr(x, 22);
}

uint sigma1(uint x) {
    return rotr(x, 6) ^ rotr(x, 11) ^ rotr(x, 25);
}

uint gamma0(uint x) {
    return rotr(x, 7) ^ rotr(x, 18) ^ (x >> 3);
}

uint gamma1(uint x) {
    return rotr(x, 17) ^ rotr(x, 19) ^ (x >> 10);
}

// Transformación SHA-256
void sha256_transform(uint state[8], const uchar block[64]) {
    uint w[64];
    uint a, b, c, d, e, f, g, h;
    uint t1, t2;

    for (int i = 0; i < 16; ++i) {
        w[i] = (block[i * 4] << 24) | (block[i * 4 + 1] << 16) | (block[i * 4 + 2] << 8) | (block[i * 4 + 3]);
    }

    for (int i = 16; i < 64; ++i) {
        w[i] = gamma1(w[i - 2]) + w[i - 7] + gamma0(w[i - 15]) + w[i - 16];
    }

    a = state[0];
    b = state[1];
    c = state[2];
    d = state[3];
    e = state[4];
    f = state[5];
    g = state[6];
    h = state[7];

    for (int i = 0; i < 64; ++i) {
        t1 = h + sigma1(e) + ch(e, f, g) + k[i] + w[i];
        t2 = sigma0(a) + maj(a, b, c);
        h = g;
        g = f;
        f = e;
        e = d + t1;
        d = c;
        c = b;
        b = a;
        a = t1 + t2;
    }

    state[0] += a;
    state[1] += b;
    state[2] += c;
    state[3] += d;
    state[4] += e;
    state[5] += f;
    state[6] += g;
    state[7] += h;
}

// SHA-256 para __global punteros
void sha256_global(const __global uchar* data, ulong length, __global uchar* hash) {
    uint state[8] = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    };

    uchar block[64];
    ulong bit_len = length * 8;
    uint i, j;

    for (i = 0; i < length; ++i) {
        block[i % 64] = data[i];
        if ((i % 64) == 63) {
            sha256_transform(state, block);
        }
    }

    block[i % 64] = 0x80;
    if ((i % 64) >= 56) {
        sha256_transform(state, block);
        for (int k = 0; k < 56; ++k) block[k] = 0;
    } else {
        for (int k = (i % 64) + 1; k < 56; ++k) block[k] = 0;
    }

    for (j = 0; j < 8; ++j) {
        block[56 + j] = bit_len >> (56 - 8 * j);
    }
    sha256_transform(state, block);

    for (i = 0; i < 8; ++i) {
        hash[4 * i] = (state[i] >> 24) & 0xff;
        hash[4 * i + 1] = (state[i] >> 16) & 0xff;
        hash[4 * i + 2] = (state[i] >> 8) & 0xff;
        hash[4 * i + 3] = state[i] & 0xff;
    }
}

// SHA-256 para __private punteros
void sha256_private(const __private uchar* data, ulong length, __private uchar* hash) {
    uint state[8] = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    };

    uchar block[64];
    ulong bit_len = length * 8;
    uint i, j;

    for (i = 0; i < length; ++i) {
        block[i % 64] = data[i];
        if ((i % 64) == 63) {
            sha256_transform(state, block);
        }
    }

    block[i % 64] = 0x80;
    if ((i % 64) >= 56) {
        sha256_transform(state, block);
        for (int k = 0; k < 56; ++k) block[k] = 0;
    } else {
        for (int k = (i % 64) + 1; k < 56; ++k) block[k] = 0;
    }

    for (j = 0; j < 8; ++j) {
        block[56 + j] = bit_len >> (56 - 8 * j);
    }
    sha256_transform(state, block);

    for (i = 0; i < 8; ++i) {
        hash[4 * i] = (state[i] >> 24) & 0xff;
        hash[4 * i + 1] = (state[i] >> 16) & 0xff;
        hash[4 * i + 2] = (state[i] >> 8) & 0xff;
        hash[4 * i + 3] = state[i] & 0xff;
    }
}

// Swap Endian para __global punteros
void swap_endian_global(__global uchar* hash, __global uchar* swapped_hash) {
    for (int i = 0; i < 32; i++) {
        swapped_hash[i] = hash[31 - i];
    }
}

// Swap Endian para __private punteros
void swap_endian_private(const __private uchar* hash, __private uchar* swapped_hash) {
    for (int i = 0; i < 32; i++) {
        swapped_hash[i] = hash[31 - i];
    }
}

// Definir MAX_HASHES
#define MAX_HASHES 10

// Kernel para el doble hash SHA-256
__kernel void sha256_double_hash(__global const uchar* data, __global uchar* result, __global const ulong* data_lengths, __global const ulong* offsets, ulong num_elements) {
    ulong idx = get_global_id(0);
    if (idx >= num_elements) return;

    const __global uchar* input_data = data + offsets[idx];
    ulong input_length = data_lengths[idx];

    __private uchar private_input[80]; // assuming max input length of 80
    for (ulong i = 0; i < input_length; ++i) {
        private_input[i] = input_data[i];
    }

    __private uchar intermediate_hash[32];
    __private uchar final_hash[32];
    __private uchar swapped_hash[32];

    sha256_private(private_input, input_length, intermediate_hash);
    sha256_private(intermediate_hash, 32, final_hash);
    swap_endian_private(final_hash, swapped_hash);

    for (int i = 0; i < 32; ++i) {
        result[idx * 32 + i] = swapped_hash[i];
    }
}

// Kernel para filtrar hashes que comienzan con ceros en nibbles
__kernel void filter_hashes(__global const uchar* hashes, __global const uchar* data, __global uchar* filtered_hashes, __global uchar* filtered_data, __global int* output_count, ulong num_elements, int num_zeros) {
    ulong idx = get_global_id(0);
    if (idx >= num_elements) return;

    int zero_count = 0;
    for (int i = 0; i < num_zeros; ++i) {
        uchar nibble;
        if (i % 2 == 0) {
            // High nibble
            nibble = (hashes[idx * 32 + i / 2] >> 4) & 0x0F;
        } else {
            // Low nibble
            nibble = hashes[idx * 32 + i / 2] & 0x0F;
        }

        if (nibble == 0) {
            zero_count++;
        } else {
            break;
        }
    }

    if (zero_count == num_zeros) {
        int pos = atomic_add(output_count, 1);
        if (pos < MAX_HASHES) {
            for (int i = 0; i < 32; ++i) {
                filtered_hashes[pos * 32 + i] = hashes[idx * 32 + i];
            }
            for (int i = 0; i < 80; ++i) {  // Asumiendo que los datos originales tienen 80 bytes
                filtered_data[pos * 80 + i] = data[idx * 80 + i];
            }
        }
    }
}

"""

# Inicialización de OpenCL
platform = cl.get_platforms()[0]
device = platform.get_devices()[0]
context = cl.Context([device])
queue = cl.CommandQueue(context)
program = cl.Program(context, kernel_code).build()

# Función principal para ejecutar los kernels
def sha256_opencl(data_list, num_zeros):
    concatenated_data = b''.join(data_list)
    data_lengths = np.array([len(data) for data in data_list], dtype=np.uint64)
    offsets = np.cumsum([0] + list(data_lengths[:-1])).astype(np.uint64)

    mf = cl.mem_flags
    data_buf = cl.Buffer(context, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=concatenated_data)
    data_lengths_buf = cl.Buffer(context, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=data_lengths)
    offsets_buf = cl.Buffer(context, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=offsets)
    result_buf = cl.Buffer(context, mf.WRITE_ONLY, 32 * len(data_list))

    MAX_HASHES = 10
    filtered_hashes_buf = cl.Buffer(context, mf.WRITE_ONLY, 32 * MAX_HASHES)
    filtered_data_buf = cl.Buffer(context, mf.WRITE_ONLY, 80 * MAX_HASHES)
    output_count_buf = cl.Buffer(context, mf.READ_WRITE | mf.COPY_HOST_PTR, hostbuf=np.array([0], dtype=np.int32))

    BLOCK_SIZE = 256
    num_elements = len(data_list)
    global_size = (num_elements + BLOCK_SIZE - 1) // BLOCK_SIZE * BLOCK_SIZE

    sha256_double_hash_kernel = program.sha256_double_hash
    sha256_double_hash_kernel.set_args(data_buf, result_buf, data_lengths_buf, offsets_buf, np.uint64(num_elements))
    cl.enqueue_nd_range_kernel(queue, sha256_double_hash_kernel, (global_size,), (BLOCK_SIZE,))

    queue.finish()

    filter_hashes_kernel = program.filter_hashes
    filter_hashes_kernel.set_args(result_buf, data_buf, filtered_hashes_buf, filtered_data_buf, output_count_buf, np.uint64(num_elements), np.int32(num_zeros))
    cl.enqueue_nd_range_kernel(queue, filter_hashes_kernel, (global_size,), (BLOCK_SIZE,))

    queue.finish()

    output_count = np.empty(1, dtype=np.int32)
    cl.enqueue_copy(queue, output_count, output_count_buf).wait()
    num_valid_hashes = min(output_count[0], MAX_HASHES)
    filtered_hashes = np.empty((num_valid_hashes, 32), dtype=np.uint8)
    filtered_data = np.empty((num_valid_hashes, 80), dtype=np.uint8)

    if num_valid_hashes > 0:
        cl.enqueue_copy(queue, filtered_hashes, filtered_hashes_buf).wait()
        cl.enqueue_copy(queue, filtered_data, filtered_data_buf).wait()

    # print(f"{num_valid_hashes} hashes cumplen con la condición.")
    results = [
        {
            "data": ''.join(format(byte, '02x') for byte in data_row),
            "hash": ''.join(format(byte, '02x') for byte in hash_row)
        }
        for data_row, hash_row in zip(filtered_data, filtered_hashes)
    ]
    return results


def sha256_pyopencl(data_array, num_zeros):
    data_list = [bytes.fromhex(row) for row in data_array]

    # start_time = time.time()
    results = sha256_opencl(data_list, num_zeros)
    # end_time = time.time()

    # Mostrar algunos resultados y estadísticas
    # for result in results[:10]:  # Mostrar solo los primeros 10 para ahorrar espacio
    #     print(f"Data: {result['data']} -> Hash: {result['hash']}")
    # print(f"El programa tomó {end_time - start_time} segundos en ejecutarse")
    return results

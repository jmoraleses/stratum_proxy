import pycuda.autoinit
import pycuda.driver as cuda
import pycuda.compiler as compiler
import numpy as np

# Código CUDA
kernel_code = """
#include <stdint.h>
#include <string.h>

#define MAX_HASHES 10

// Constantes SHA-256
__device__ __constant__ uint32_t k[64] = {
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
__device__ uint32_t rotr(uint32_t x, uint32_t n) {
    return (x >> n) | (x << (32 - n));
}

// Funciones auxiliares SHA-256
__device__ uint32_t ch(uint32_t x, uint32_t y, uint32_t z) {
    return (x & y) ^ (~x & z);
}

__device__ uint32_t maj(uint32_t x, uint32_t y, uint32_t z) {
    return (x & y) ^ (x & z) ^ (y & z);
}

__device__ uint32_t sigma0(uint32_t x) {
    return rotr(x, 2) ^ rotr(x, 13) ^ rotr(x, 22);
}

__device__ uint32_t sigma1(uint32_t x) {
    return rotr(x, 6) ^ rotr(x, 11) ^ rotr(x, 25);
}

__device__ uint32_t gamma0(uint32_t x) {
    return rotr(x, 7) ^ rotr(x, 18) ^ (x >> 3);
}

__device__ uint32_t gamma1(uint32_t x) {
    return rotr(x, 17) ^ rotr(x, 19) ^ (x >> 10);
}

// Transformación SHA-256
__device__ void sha256_transform(uint32_t state[8], const uint8_t block[64]) {
    uint32_t w[64];
    uint32_t a, b, c, d, e, f, g, h;
    uint32_t t1, t2;

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

__device__ void sha256(const uint8_t* data, uint64_t length, uint8_t* hash) {
    uint32_t state[8] = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    };

    uint8_t block[64];
    uint64_t bit_len = length * 8;
    uint32_t i, j;

    for (i = 0; i < length; ++i) {
        block[i % 64] = data[i];
        if ((i % 64) == 63) {
            sha256_transform(state, block);
        }
    }

    block[i % 64] = 0x80;
    if ((i % 64) >= 56) {
        sha256_transform(state, block);
        memset(block, 0, 56);
    } else {
        memset(block + (i % 64) + 1, 0, 55 - (i % 64));
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

// Swap Endian
__device__ void swap_endian(uint8_t* hash, uint8_t* swapped_hash) {
    for (int i = 0; i < 32; i++) {
        swapped_hash[i] = hash[31 - i];
    }
}

// Kernel para el doble hash SHA-256
extern "C" __global__ void sha256_double_hash(const uint8_t* data, uint8_t* result, const uint64_t* data_lengths, const uint64_t* offsets, uint64_t num_elements) {
    uint64_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= num_elements) return;

    const uint8_t* input_data = data + offsets[idx];
    uint64_t input_length = data_lengths[idx];

    uint8_t intermediate_hash[32];
    uint8_t final_hash[32];
    uint8_t swapped_hash[32];

    sha256(input_data, input_length, intermediate_hash);
    sha256(intermediate_hash, 32, final_hash);
    swap_endian(final_hash, swapped_hash);

    for (int i = 0; i < 32; ++i) {
        result[idx * 32 + i] = swapped_hash[i];
    }
}

// Kernel para filtrar hashes que comienzan con ceros en nibbles
extern "C" __global__ void filter_hashes(const uint8_t* hashes, const uint8_t* data, uint8_t* filtered_hashes, uint8_t* filtered_data, int32_t* output_count, uint64_t num_elements, int32_t num_zeros) {
    uint64_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= num_elements) return;

    int zero_count = 0;
    for (int i = 0; i < num_zeros; ++i) {
        uint8_t nibble;
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
        int pos = atomicAdd(output_count, 1);
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

# Función para verificar si un dispositivo CUDA está libre
def is_device_free(device_id):
    try:
        # Intentar crear un contexto en el dispositivo
        context = cuda.Device(device_id).make_context()
        context.detach()
        return True
    except:
        return False

# Seleccionar un dispositivo CUDA libre
selected_device = None
num_devices = cuda.Device.count()

for device_id in range(num_devices):
    if is_device_free(device_id):
        selected_device = cuda.Device(device_id)
        break

if selected_device is None:
    raise RuntimeError("No hay dispositivos CUDA disponibles o libres.")

# Compilar el kernel CUDA
mod = compiler.SourceModule(kernel_code)

# Función principal para ejecutar los kernels
def sha256_cuda(data_list, num_zeros):
    context = cuda.Device(selected_device).make_context()
    try:
        concatenated_data = b''.join(data_list)
        data_lengths = np.array([len(data) for data in data_list], dtype=np.uint64)
        offsets = np.cumsum([0] + list(data_lengths[:-1])).astype(np.uint64)

        data_gpu = cuda.mem_alloc(len(concatenated_data))
        data_lengths_gpu = cuda.mem_alloc(data_lengths.nbytes)
        offsets_gpu = cuda.mem_alloc(offsets.nbytes)
        result_gpu = cuda.mem_alloc(32 * len(data_list))

        MAX_HASHES = 10
        filtered_hashes_gpu = cuda.mem_alloc(32 * MAX_HASHES)
        filtered_data_gpu = cuda.mem_alloc(80 * MAX_HASHES)
        output_count_gpu = cuda.mem_alloc(np.int32().nbytes)

        cuda.memcpy_htod(data_gpu, concatenated_data)
        cuda.memcpy_htod(data_lengths_gpu, data_lengths)
        cuda.memcpy_htod(offsets_gpu, offsets)
        cuda.memcpy_htod(output_count_gpu, np.array([0], dtype=np.int32))

        BLOCK_SIZE = 256
        num_elements = len(data_list)
        grid = ((num_elements + BLOCK_SIZE - 1) // BLOCK_SIZE, 1, 1)

        sha256_double_hash = mod.get_function("sha256_double_hash")
        filter_hashes = mod.get_function("filter_hashes")

        sha256_double_hash(data_gpu, result_gpu, data_lengths_gpu, offsets_gpu, np.uint64(num_elements),
                           block=(BLOCK_SIZE, 1, 1), grid=grid)
        cuda.Context.synchronize()

        num_zeros_np = np.int32(num_zeros)
        filter_hashes(result_gpu, data_gpu, filtered_hashes_gpu, filtered_data_gpu, output_count_gpu,
                      np.uint64(num_elements), num_zeros_np, block=(BLOCK_SIZE, 1, 1), grid=grid)
        cuda.Context.synchronize()

        output_count = np.empty(1, dtype=np.int32)
        cuda.memcpy_dtoh(output_count, output_count_gpu)
        num_valid_hashes = min(output_count[0], MAX_HASHES)
        filtered_hashes = np.empty((num_valid_hashes, 32), dtype=np.uint8)
        filtered_data = np.empty((num_valid_hashes, 80), dtype=np.uint8)

        if num_valid_hashes > 0:
            cuda.memcpy_dtoh(filtered_hashes, filtered_hashes_gpu)
            cuda.memcpy_dtoh(filtered_data, filtered_data_gpu)

        data_gpu.free()
        data_lengths_gpu.free()
        offsets_gpu.free()
        result_gpu.free()
        filtered_hashes_gpu.free()
        filtered_data_gpu.free()
        output_count_gpu.free()

        # print(f"{num_valid_hashes} hashes cumplen con la condición.")
        results = [
            {
                "data": ''.join(format(byte, '02x') for byte in data_row),
                "hash": ''.join(format(byte, '02x') for byte in hash_row)
            }
            for data_row, hash_row in zip(filtered_data, filtered_hashes)
        ]
        return results
    finally:
        context.pop()
        context.detach()

# Función principal para probar la función de hash CUDA
def sha256_pycuda(data_array, num_zeros):
    data_list = [bytes.fromhex(row) for row in data_array]
    results = sha256_cuda(data_list, num_zeros)
    return results
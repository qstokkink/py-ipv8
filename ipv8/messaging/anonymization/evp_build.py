"""
Port of code by @hbiyik:
https://github.com/hbiyik/evp/blob/master/asyncaead/evp.pyx
https://github.com/hbiyik/evp/blob/master/asyncaead/evp.pxd
"""

from cffi import FFI

ffibuilder = FFI()

ffibuilder.cdef(r"""
unsigned char encode(unsigned char * output, int outputlen, unsigned char * datain, int inputlen,
                     unsigned char * key, unsigned char * iv, int ivlen, unsigned char * tag);
unsigned char decode(unsigned char * output, int outputlen, unsigned char * datain, int inputlen,
                     unsigned char * key, unsigned char * iv, int ivlen, unsigned char * tag);                     
""")

ffibuilder.cdef("static int tls_tag_len;")

ffibuilder.set_source("_evp", r"""
#include <openssl/evp.h>
#include <openssl/err.h>

int tls_tag_len = EVP_CHACHAPOLY_TLS_TAG_LEN;

static unsigned char encode(unsigned char * output,
                            int outputlen, 
                            unsigned char * datain,
                            int inputlen,
                            unsigned char * key,
                            unsigned char * iv,
                            int ivlen,
                            unsigned char * tag)
{
    unsigned char err = 0;
    EVP_CIPHER_CTX * ctx = EVP_CIPHER_CTX_new();
    
    if (ctx == 0)
        err = 1;
    else if (EVP_CipherInit_ex(ctx, EVP_chacha20_poly1305(), 0, key, iv, 1) == 0)
        err = 2;
    else if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_AEAD_SET_IVLEN, ivlen, 0) == 0)
        err = 3;
    else if (EVP_CipherUpdate(ctx, output, &outputlen, datain, inputlen) == 0)
        err = 4;
    else if (EVP_CipherFinal_ex(ctx, output, &outputlen) == 0)
        err = 5;
    else if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_AEAD_GET_TAG, EVP_CHACHAPOLY_TLS_TAG_LEN, tag) == 0)
        err = 6;
    
    EVP_CIPHER_CTX_free(ctx);
    return err;
}

static unsigned char decode(unsigned char * output,
                            int outputlen, 
                            unsigned char * datain,
                            int inputlen,
                            unsigned char * key,
                            unsigned char * iv,
                            int ivlen,
                            unsigned char * tag)
{
    unsigned char err = 0;
    EVP_CIPHER_CTX * ctx = EVP_CIPHER_CTX_new();
    
    if (ctx == 0)
        err = 1;
    else if (EVP_CipherInit_ex(ctx, EVP_chacha20_poly1305(), 0, key, iv, 0) == 0)
        err = 2;
    else if (EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_AEAD_SET_IVLEN, ivlen, 0) == 0)
        err = 3;
    else if (EVP_CipherUpdate(ctx, output, &outputlen, datain, inputlen) == 0)
        err = 4;
    else if (EVP_CipherFinal_ex(ctx, output, &outputlen) == 0)
        err = 5;
    
    EVP_CIPHER_CTX_free(ctx);
    return err;
}
""", libraries=["crypto"])


def build():
    ffibuilder.compile(verbose=True)

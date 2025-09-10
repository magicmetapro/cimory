import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
from io import BytesIO
import PyPDF2
import requests
import time
import re
from fuzzywuzzy import fuzz, process

# Konfigurasi halaman
st.set_page_config(
    page_title="Ekstraksi Faktur",
    page_icon="📦",
    layout="wide"
)

# Inisialisasi Gemini dengan API key yang disediakan
api_key = "AIzaSyBzKrjj-UwAVm-0MEjfSx3ShnJ4fDrsACU"

try:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')
except Exception as e:
    st.error(f"❌ Error inisialisasi Gemini: {str(e)}")
    st.stop()

# URL database Scylla - PASTIKAN URL INI BENAR
SCYLLA_DATABASE_URL = "https://raw.githubusercontent.com/magicmetapro/q/refs/heads/main/itemscyllaV.json"

# Fungsi untuk memuat data Scylla dari URL
@st.cache_data(ttl=3600)  # Cache selama 1 jam
def load_scylla_data():
    try:
        # Hapus pesan yang menampilkan URL
        # st.info(f"🔗 Mengambil data dari: {SCYLLA_DATABASE_URL}")
        response = requests.get(SCYLLA_DATABASE_URL)
        response.raise_for_status()  # Akan raise exception untuk status code 4xx/5xx
        data = response.json()
        
        # Membuat dictionary untuk mapping kode barang ke Scylla
        scylla_mapping = {}
        for item in data:
            kode_barang = item.get("ItemDescription", "")
            scylla = item.get("Scylla", "")
            if kode_barang and scylla:
                scylla_mapping[kode_barang] = scylla
                
        st.success(f"✅ Berhasil memuat {len(scylla_mapping)} item dari database Scylla")
        return scylla_mapping
        
    except requests.exceptions.RequestException as e:
        st.error(f"❌ Gagal mengakses database Scylla: {str(e)}")
        st.error(f"URL: {SCYLLA_DATABASE_URL}")
        return {}
    except json.JSONDecodeError as e:
        st.error(f"❌ Format JSON tidak valid: {str(e)}")
        return {}
    except Exception as e:
        st.error(f"❌ Error tidak terduga: {str(e)}")
        return {}

# Fungsi untuk normalisasi teks (menghapus spasi berlebih, mengubah ke lowercase, dll)
def normalize_text(text):
    if not isinstance(text, str):
        return ""
    
    # Ubah ke lowercase
    text = text.lower()
    
    # Hapus spasi berlebih
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Hapus karakter khusus yang tidak perlu
    text = re.sub(r'[^\w\s]', '', text)
    
    # Hapus kata-kata umum yang mungkin bervariasi
    text = re.sub(r'\b(ml|new|r|special|edition)\b', '', text)
    
    # Hapus spasi berlebih lagi
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

# Fungsi untuk mendapatkan kode Scylla berdasarkan kode barang dengan pencocokan fleksibel
def get_scylla_code(item_description, scylla_mapping):
    if not item_description or not scylla_mapping:
        return "Tidak Ditemukan"
    
    # Coba pencocokan exact pertama
    if item_description in scylla_mapping:
        return scylla_mapping[item_description]
    
    # Normalisasi input
    normalized_input = normalize_text(item_description)
    
    # Coba pencocokan dengan normalisasi
    for key, value in scylla_mapping.items():
        normalized_key = normalize_text(key)
        if normalized_input == normalized_key:
            return value
    
    # Jika masih tidak ditemukan, gunakan fuzzy matching
    best_match = None
    best_score = 0
    
    for key in scylla_mapping.keys():
        # Hitung similarity score
        score = fuzz.ratio(normalize_text(item_description), normalize_text(key))
        
        if score > best_score and score > 92:  # Threshold 90% similarity
            best_score = score
            best_match = scylla_mapping[key]
    
    if best_match:
        return f"{best_match} (Fuzzy Match: {best_score}%)"
    
    return "Tidak Ditemukan"

# Fungsi untuk memformat kode barang TANPA tanda kutip di depan
def format_item_description(kode):
    # Jika kode diawali dengan tanda kutip, hapus
    if isinstance(kode, str) and kode.startswith("'"):
        kode = kode[1:]
    # Jika kode diawali dengan tanda kutip ganda, hapus
    if isinstance(kode, str) and kode.startswith('"'):
        kode = kode[1:-1] if kode.endswith('"') else kode[1:]
    return kode

# Fungsi untuk memproses satu file PDF dan ekstrak data yang dibutuhkan
def process_single_pdf(uploaded_file, scylla_mapping):
    try:
        # Ekstrak teks dari PDF
        pdf_reader = PyPDF2.PdfReader(uploaded_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        
        # Gunakan Gemini untuk menganalisis teks yang sudah diekstrak
        prompt = """
        Analisis dokumen PDF faktur ini dan ekstrak data dalam format JSON yang valid dengan struktur:
        [
          {
            "No": "nomor urut",
            "ItemDescription": "nama item",
            "Qty": "jumlah quantity",
            "Qty Pack": "jumlah pack",
            "Unit Pack": "unit pack"
          },
          ...
        ]
        
        Pastikan untuk:
        1. Hanya mengekstrak data yang relevan dari tabel
        2. Mengembalikan format JSON yang valid
        3. Mengonversi Qty dan Qty Pack ke angka (bukan string)
        4. Format ItemDescription TANPA diawali dengan tanda kutip tunggal (')
        5. Ekstrak semua kolom: No, Item Description, Qty, Qty Pack, Unit Pack
        6. Abaikan informasi batch dan weight jika ada
        """
        
        if text.strip():
            response = model.generate_content(prompt + "\n\nIni adalah teks dari PDF:\n" + text)
        else:
            # Jika tidak bisa mengekstrak teks, coba baca sebagai file biner
            uploaded_file.seek(0)
            pdf_content = uploaded_file.read()
            response = model.generate_content([
                prompt,
                {"mime_type": "application/pdf", "data": pdf_content}
            ])
        
        # Proses respons
        if response.text:
            # Cari JSON dalam respon
            json_start = response.text.find('[')
            json_end = response.text.rfind(']') + 1
            
            if json_start != -1 and json_end != -1:
                json_str = response.text[json_start:json_end]
                parsed = json.loads(json_str)
                
                # Tambahkan Scylla code untuk setiap item
                for item in parsed:
                    item['Scylla'] = get_scylla_code(item['ItemDescription'], scylla_mapping)
                
                return parsed, text[:1000] + "..." if len(text) > 1000 else text
            else:
                return None, f"Tidak dapat menemukan format JSON dalam respons: {response.text}"
        else:
            return None, "Tidak ada respons dari Gemini."
            
    except Exception as e:
        return None, f"Error dalam pemrosesan PDF: {str(e)}"

st.title("📦 Ekstraksi Data Faktur")

# Sidebar untuk update database
with st.sidebar:
    st.header("⚙️ Pengaturan")
    
    # Tombol untuk memperbarui database
    if st.button("🔄 Update Database Scylla", use_container_width=True):
        with st.spinner("Memperbarui database Scylla..."):
            # Hapus cache untuk memaksa pembaruan data
            st.cache_data.clear()
            
            # Muat ulang data
            updated_scylla_mapping = load_scylla_data()
            
            if updated_scylla_mapping:
                st.success("✅ Database berhasil diperbarui!")
                st.session_state.scylla_mapping = updated_scylla_mapping
                st.write(f"Jumlah data terbaru: {len(updated_scylla_mapping)} kode barang")
                
                # Tampilkan beberapa contoh data
                with st.expander("Lihat contoh data"):
                    sample_items = list(updated_scylla_mapping.items())[:5]
                    for item, scylla in sample_items:
                        st.write(f"**{item}** → `{scylla}`")
            else:
                st.error("❌ Gagal memperbarui database. Periksa koneksi internet atau URL database.")
    
    st.markdown("---")
    st.info("Klik tombol di atas untuk memperbarui database Scylla dari sumber terbaru.")

# Memuat data Scylla
if 'scylla_mapping' not in st.session_state:
    with st.spinner("Memuat database Scylla pertama kali..."):
        st.session_state.scylla_mapping = load_scylla_data()

scylla_mapping = st.session_state.scylla_mapping

# Tampilkan info database di sidebar
if scylla_mapping:
    st.sidebar.success(f"✅ Database siap: {len(scylla_mapping)} item")
else:
    st.sidebar.warning("⚠️ Database belum berhasil dimuat")

st.header("Ekstraksi Data dari Faktur PDF")

# Upload multiple PDFs
uploaded_files = st.file_uploader("Upload Faktur PDF (Bisa multiple)", type=["pdf"], key="pdf_uploader", accept_multiple_files=True)

if uploaded_files:
    st.success(f"✅ {len(uploaded_files)} PDF berhasil diupload")
    
    # Tampilkan info tentang kolom yang akan diekstrak
    with st.expander("ℹ️ Kolom yang akan diekstrak"):
        st.write("""
        Sistem akan mengekstrak 5 kolom berikut dari PDF:
        1. **No** - Nomor urut item
        2. **Item Description** - Deskripsi produk
        3. **Scylla** - Kode Scylla dari database
        4. **Qty** - Quantity/jumlah
        5. **Qty Pack** - Jumlah pack
        6. **Unit Pack** - Unit pack (biasanya 'Cin')
        """)
    
    if st.button("Ekstrak Data dari Semua Faktur", type="primary"):
        # Pastikan database sudah dimuat
        if not scylla_mapping:
            st.error("❌ Database Scylla belum dimuat. Silakan klik 'Update Database Scylla' terlebih dahulu.")
            st.stop()
            
        all_results = []
        extracted_texts = []
        file_names = []
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, uploaded_file in enumerate(uploaded_files):
            status_text.text(f"Memproses {i+1}/{len(uploaded_files)}: {uploaded_file.name}...")
            progress_bar.progress((i) / len(uploaded_files))
            
            result, extracted_text = process_single_pdf(uploaded_file, scylla_mapping)
            
            if result:
                # Tambahkan nama file ke setiap item
                for item in result:
                    item['nama_file'] = uploaded_file.name
                
                all_results.extend(result)
                file_names.append(uploaded_file.name)
            
            extracted_texts.append({
                'nama_file': uploaded_file.name,
                'text': extracted_text
            })
            
            # Jeda singkat untuk menghindari rate limiting
            time.sleep(0.5)
            
        progress_bar.progress(1.0)
        status_text.text("✅ Pemrosesan selesai!")
        
        # Tampilkan hasil
        if all_results:
            st.subheader("📊 Hasil Ekstraksi Semua PDF")
            
            # Konversi ke DataFrame
            df = pd.DataFrame(all_results)
            
            # Atur urutan kolom: No, ItemDescription, Scylla, Qty, Qty Pack, Unit Pack, nama_file
            # Scylla sekarang ditempatkan tepat setelah ItemDescription
            column_order = ['No', 'ItemDescription', 'Scylla', 'Qty', 'Qty Pack', 'Unit Pack', 'nama_file']
            # Hanya ambil kolom yang ada di DataFrame
            column_order = [col for col in column_order if col in df.columns]
            df = df[column_order]
            
            # Tampilkan DataFrame
            st.dataframe(df)
            
            # Hitung statistik
            total_items = len(df)
            total_qty = df['Qty'].sum() if 'Qty' in df.columns else 0
            
            # Hitung match types untuk Scylla
            if 'Scylla' in df.columns:
                exact_matches = len(df[df['Scylla'].str.contains('Fuzzy Match') == False])
                fuzzy_matches = len(df[df['Scylla'].str.contains('Fuzzy Match')])
                not_found = len(df[df['Scylla'] == 'Tidak Ditemukan'])
            else:
                exact_matches = fuzzy_matches = not_found = 0
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total File Diproses", len(uploaded_files))
            col2.metric("Total Items", total_items)
            col3.metric("Total Quantity", total_qty)
            col4.metric("Exact Matches", exact_matches)
            
            if fuzzy_matches > 0:
                st.info(f"🔍 {fuzzy_matches} item menggunakan fuzzy matching")
            if not_found > 0:
                st.warning(f"⚠️ {not_found} item tidak ditemukan dalam database Scylla")
            
            # Opsi untuk mendownload hasil sebagai Excel
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                # Tulis data utama
                df.to_excel(writer, index=False, sheet_name='Data Faktur')
                workbook = writer.book
                worksheet = writer.sheets['Data Faktur']
                
                # Format untuk kode barang sebagai teks biasa
                text_format = workbook.add_format({'num_format': '@'})
                if 'ItemDescription' in df.columns:
                    worksheet.set_column('B:B', None, text_format)  # ItemDescription
                if 'Scylla' in df.columns:
                    worksheet.set_column('C:C', None, text_format)  # Scylla (sekarang di kolom C)
                
                # Tambahkan sheet dengan teks yang diekstrak
                if extracted_texts:
                    text_df = pd.DataFrame(extracted_texts)
                    text_df.to_excel(writer, index=False, sheet_name='Teks Ekstrak')
            
            excel_data = output.getvalue()
            
            st.download_button(
                label="📥 Download Hasil sebagai Excel",
                data=excel_data,
                file_name="hasil_ekstraksi_faktur.xlsx",
                mime="application/vnd.ms-excel"
            )
            
            # Tampilkan preview teks yang diekstrak
            with st.expander("📋 Teks yang Diekstrak dari Semua PDF"):
                for text_data in extracted_texts:
                    st.write(f"**File: {text_data['nama_file']}**")
                    if isinstance(text_data['text'], str) and not text_data['text'].startswith("Error"):
                        st.text_area("", text_data['text'], height=150, key=text_data['nama_file'])
                    else:
                        st.warning(text_data['text'])
                    st.markdown("---")
        else:
            st.warning("Tidak ada data yang berhasil diekstraksi dari faktur.")
            
            # Tampilkan error messages jika ada
            with st.expander("Detail Error"):
                for text_data in extracted_texts:
                    if isinstance(text_data['text'], str) and text_data['text'].startswith("Error"):
                        st.error(f"{text_data['nama_file']}: {text_data['text']}")

# Tambahkan footer
st.markdown("---")
st.markdown("**Aplikasi Ekstraksi Faktur** - Dibuat dengan Streamlit dan Gemini AI")

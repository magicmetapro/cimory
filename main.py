import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
from io import BytesIO
import PyPDF2
import requests
import time

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

# Fungsi untuk memuat data Scylla dari URL
@st.cache_data(ttl=3600)  # Cache selama 1 jam
def load_scylla_data():
    url = "https://raw.githubusercontent.com/magicmetapro/cimory/refs/heads/main/cimory.json"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        # Membuat dictionary untuk mapping kode barang ke Scylla
        scylla_mapping = {}
        for item in data:
            kode_barang = item.get("KodeBarang", "")
            scylla = item.get("Scylla", "")
            if kode_barang and scylla:
                scylla_mapping[kode_barang] = scylla
                
        return scylla_mapping
    except Exception as e:
        st.error(f"Gagal memuat data Scylla: {str(e)}")
        return {}

# Fungsi untuk mendapatkan kode Scylla berdasarkan kode barang
def get_scylla_code(kode_barang, scylla_mapping):
    # Cari kode yang sesuai dalam data Scylla
    return scylla_mapping.get(kode_barang, "Tidak Ditemukan")

# Fungsi untuk memformat kode barang TANPA tanda kutip di depan
def format_kode_barang(kode):
    # Jika kode diawali dengan tanda kutip, hapus
    if isinstance(kode, str) and kode.startswith("'"):
        return kode[1:]
    # Jika kode diawali dengan tanda kutip ganda, hapus
    if isinstance(kode, str) and kode.startswith('"'):
        return kode[1:-1] if kode.endswith('"') else kode[1:]
    return kode

# Fungsi untuk memproses satu file PDF
def process_single_pdf(uploaded_file, scylla_mapping):
    try:
        # Ekstrak teks dari PDF
        pdf_reader = PyPDF2.PdfReader(uploaded_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        
        # Gunakan Gemini untuk menganalisis teks yang sudah diekstrak
        prompt = """
        Analisis dokumen PDF faktur ini dan ekstrak semua kode barang (SKU) dan kuantum (jumlah karton/CTN).
        Format output harus JSON yang valid:
        [{"kode_barang": "kode1", "kuantum": angka}, {"kode_barang": "kode2", "kuantum": angka}, ...]
        
        Pastikan untuk:
        1. Hanya mengekstrak data yang relevan
        2. Mengembalikan format JSON yang valid
        3. Mengonversi kuantum ke angka (bukan string)
        4. Format kode_barang TANPA diawali dengan tanda kutip tunggal (')
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
                
                # Format kode_barang TANPA tanda kutip di depan dan tambahkan Scylla
                for item in parsed:
                    item['kode_barang'] = format_kode_barang(item['kode_barang'])
                    item['scylla'] = get_scylla_code(item['kode_barang'], scylla_mapping)
                
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
            else:
                st.error("❌ Gagal memperbarui database")
    
    st.markdown("---")
    st.info("Klik tombol di atas untuk memperbarui database Scylla dari sumber terbaru.")

# Memuat data Scylla
if 'scylla_mapping' not in st.session_state:
    st.session_state.scylla_mapping = load_scylla_data()

scylla_mapping = st.session_state.scylla_mapping

st.header("Ekstraksi Data dari Faktur PDF (Bulk Processing)")

# Upload multiple PDFs
uploaded_files = st.file_uploader("Upload Faktur PDF (Bisa multiple)", type=["pdf"], key="pdf_uploader", accept_multiple_files=True)

if uploaded_files:
    st.success(f"✅ {len(uploaded_files)} PDF berhasil diupload")
    
    if st.button("Ekstrak Data dari Semua Faktur", type="primary"):
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
            
        progress_bar.progress(1.0)
        status_text.text("✅ Pemrosesan selesai!")
        
        # Tampilkan hasil
        if all_results:
            st.subheader("📊 Hasil Ekstraksi Semua PDF")
            
            # Konversi ke DataFrame dan atur urutan kolom
            df = pd.DataFrame(all_results)
            
            # Atur urutan kolom: kode_barang, scylla, kuantum, nama_file
            column_order = ['kode_barang', 'scylla', 'kuantum', 'nama_file']
            df = df[column_order]
            
            # Tampilkan DataFrame
            st.dataframe(df)
            
            # Hitung statistik
            total_kuantum = df['kuantum'].sum()
            ditemukan = len(df[df['scylla'] != 'Tidak Ditemukan'])
            total_barang = len(df)
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Total File Diproses", len(uploaded_files))
            col2.metric("Total Kuantum", total_kuantum)
            col3.metric("Kode Scylla Ditemukan", f"{ditemukan} dari {total_barang}")
            
            # Opsi untuk mendownload hasil sebagai Excel dengan jarak antar file
            output = BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                # Tulis data utama
                df.to_excel(writer, index=False, sheet_name='Data Faktur')
                workbook = writer.book
                worksheet = writer.sheets['Data Faktur']
                
                # Format untuk kode barang sebagai teks biasa
                text_format = workbook.add_format({'num_format': '@'})
                worksheet.set_column('A:A', None, text_format)  # kode_barang
                worksheet.set_column('B:B', None, text_format)  # scylla
                
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

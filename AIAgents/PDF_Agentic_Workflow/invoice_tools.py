import os
from langchain_core.tools import tool
from pypdf import PdfReader

@tool
def extract_pdf_text(pdf_path: str) -> str:
    """Extracts all text from a given PDF invoice file path."""
    try:
        if not os.path.exists(pdf_path):
            return f"Error: The file '{pdf_path}' does not exist."

        # Open and read the PDF
        reader = PdfReader(pdf_path)
        extracted_text = ""
        
        # Loop through pages and extract text
        for page in reader.pages:
            text = page.extract_text()
            if text:
                extracted_text += text + "\n"
                
        if not extracted_text.strip():
            return "Error: The PDF was read, but no text could be extracted (it might be an image-based PDF)."
            
        return extracted_text

    except PermissionError:
        return f"Error: Permission denied to read '{pdf_path}'."
    except Exception as e:
        return f"Unexpected error reading PDF: {str(e)}"

@tool
def save_billing_details(content: str, output_file: str = "billing_file.txt") -> str:
    """Saves the extracted billing details into a text file."""
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully saved billing details to '{output_file}'"
    except PermissionError:
        return f"Error: Permission denied to write to '{output_file}'."
    except Exception as e:
        return f"Unexpected error saving file: {str(e)}"

# Bundle the tools
invoice_tools = [extract_pdf_text, save_billing_details]
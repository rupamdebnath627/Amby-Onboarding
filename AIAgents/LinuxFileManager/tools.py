import os
import shutil
from langchain_core.tools import tool
from whoosh.index import create_in, open_dir, exists_in
from whoosh.fields import Schema, TEXT, ID
from whoosh.qparser import QueryParser

INDEX_DIR = "my_filesystem_index"

@tool
def list_directory(path: str = ".") -> str:
    """Lists all files and folders in the given directory path."""
    try:
        items = os.listdir(path)
        return f"Contents of '{path}':\n" + "\n".join(items) if items else f"Directory '{path}' is empty."
    except FileNotFoundError:
        return f"Error: The directory '{path}' does not exist."
    except PermissionError:
        return f"Error: Permission denied to access '{path}'."
    except Exception as e:
        return f"Unexpected error listing directory: {str(e)}"

@tool
def read_file(file_path: str) -> str:
    """Reads the contents of a specified file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return f"Error: The file '{file_path}' was not found."
    except PermissionError:
        return f"Error: Permission denied to read '{file_path}'."
    except UnicodeDecodeError:
        return f"Error: '{file_path}' appears to be a binary file or uses an unsupported encoding."
    except Exception as e:
        return f"Unexpected error reading file: {str(e)}"

@tool
def write_file(file_path: str, content: str) -> str:
    """Creates a new file or overwrites an existing one with the provided content."""
    try:
        # Ensures the parent directory exists before attempting to write
        parent_dir = os.path.dirname(file_path)
        if parent_dir and not os.path.exists(parent_dir):
            return f"Error: The target directory '{parent_dir}' does not exist."
            
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to '{file_path}'"
    except PermissionError:
        return f"Error: Permission denied to write to '{file_path}'."
    except Exception as e:
        return f"Unexpected error writing to file: {str(e)}"

@tool
def delete_item(path: str) -> str:
    """Deletes a file or an empty directory. Use with extreme caution."""
    try:
        if os.path.isfile(path):
            os.remove(path)
            return f"Successfully deleted file '{path}'"
        elif os.path.isdir(path):
            os.rmdir(path)
            return f"Successfully deleted directory '{path}'"
        else:
            return f"Error: Path '{path}' not found."
    except PermissionError:
        return f"Error: Permission denied to delete '{path}'."
    except OSError as e:
        # Catches cases where a directory is not empty
        return f"OS Error deleting item (ensure directories are empty): {str(e)}"
    except Exception as e:
        return f"Unexpected error deleting item: {str(e)}"

@tool
def move_item(source: str, destination: str) -> str:
    """Moves or renames a file or directory."""
    try:
        if not os.path.exists(source):
            return f"Error: The source '{source}' does not exist."
            
        shutil.move(source, destination)
        return f"Successfully moved '{source}' to '{destination}'"
    except PermissionError:
        return f"Error: Permission denied to move '{source}'."
    except Exception as e:
        return f"Unexpected error moving item: {str(e)}"

@tool
def create_folder(path: str) -> str:
    """Creates a new directory/folder at the specified path."""
    try:
        os.makedirs(path, exist_ok=True)
        return f"Successfully created or verified folder at '{path}'"
    except PermissionError:
        return f"Error: Permission denied to create folder at '{path}'."
    except Exception as e:
        return f"Unexpected error creating folder: {str(e)}"

def build_file_index(root_path: str = "."):
    """
    Utility function to crawl folders and build the Whoosh index.
    This function should be executed independently before starting the agent.
    """
    schema = Schema(path=ID(stored=True, unique=True), filename=TEXT(stored=True))
    
    try:
        if not os.path.exists(INDEX_DIR):
            os.mkdir(INDEX_DIR)
            
        ix = create_in(INDEX_DIR, schema)
        writer = ix.writer()
        
        print(f"Building index for '{root_path}'... this might take a moment.")
        
        for root, dirs, files in os.walk(root_path):
            for d in dirs:
                folder_path = os.path.join(root, d)
                writer.add_document(path=folder_path, filename=d)
            for f in files:
                file_path = os.path.join(root, f)
                writer.add_document(path=file_path, filename=f)
                
        writer.commit()
        print("Index built successfully!")
        
    except PermissionError as e:
        print(f"Permission Error during indexing: {str(e)}")
    except Exception as e:
        print(f"Unexpected error building index: {str(e)}")

@tool
def fast_indexed_search(query: str) -> str:
    """
    Instantly searches the pre-built Whoosh index for files or folders.
    Use this instead of recursively searching the whole drive.
    """
    if not os.path.exists(INDEX_DIR) or not exists_in(INDEX_DIR):
        return "Error: Index not built yet. The system must run the indexing script first."
        
    try:
        ix = open_dir(INDEX_DIR)
        matches = []
        
        with ix.searcher() as searcher:
            parser = QueryParser("filename", ix.schema)
            # Wraps the query in wildcards to allow partial word matching
            parsed_query = parser.parse(f"*{query}*")
            
            results = searcher.search(parsed_query, limit=50)
            
            for hit in results:
                matches.append(hit['path'])
                
        if not matches:
            return f"No results found for '{query}' in the index."
            
        return f"Found {len(matches)} instantly matching items:\n" + "\n".join(matches)
        
    except Exception as e:
        return f"Unexpected error reading search index: {str(e)}"

# Bundles the initialized tools for agent consumption.
file_tools = [list_directory, read_file, write_file, delete_item, move_item, create_folder, fast_indexed_search]
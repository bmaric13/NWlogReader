import { FileType } from 'file-type';

const validateFileUpload = (file: any) => {
  const errors: string[] = [];

  if (!file) {
    errors.push('No file provided');
  } else if (!(file instanceof Buffer)) {
    errors.push('Invalid file type');
  } else {
    const fileType = FileType.fromBuffer(file);
    if (!fileType || !['text/csv', 'application/json'].includes(fileType.mime)) {
      errors.push('Only CSV and JSON files are supported');
    }
  }

  return errors;
};

export { validateFileUpload };
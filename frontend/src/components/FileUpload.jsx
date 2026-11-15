import React, { useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { uploadFile } from '../store/complaintSlice';

const FileUpload = () => {
    const dispatch = useDispatch();
    const fileInputRef = useRef(null);
    const { status } = useSelector((state) => state.complaint);

    const handleFileChange = (e) => {
        const file = e.target.files[0];
        if (!file) return;
        dispatch(uploadFile(file));
        e.target.value = null;
    };

    const handleButtonClick = () => {
        if (status !== 'loading') {
            fileInputRef.current?.click();
        }
    };

    return (
        <div style={{ width: '100%' }}>
            <input
                type="file"
                accept="application/pdf, image/*"
                ref={fileInputRef}
                onChange={handleFileChange}
                style={{ display: 'none' }}
            />
            
            <div 
                onClick={handleButtonClick}
                style={{
                    border: '1px dashed var(--c-3a4b65)',
                    borderRadius: '8px',
                    padding: '15px 12px',
                    textAlign: 'center',
                    cursor: status === 'loading' ? 'not-allowed' : 'pointer',
                    backgroundColor: 'var(--c-172438)',
                    transition: 'border-color 0.2s, background-color 0.2s',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: '8px'
                }}
                onMouseOver={(e) => { if(status !== 'loading') e.currentTarget.style.borderColor = 'var(--c-7968b8)'; }}
                onMouseOut={(e) => { if(status !== 'loading') e.currentTarget.style.borderColor = 'var(--c-3a4b65)'; }}
            >
                {status === 'loading' ? (
                    <span style={{ color: 'var(--c-aab7c9)', fontSize: '13px', fontWeight: '500' }}>Processing Document...</span>
                ) : (
                    <>
                        <svg viewBox="0 0 24 24" fill="none" stroke="var(--c-aab7c9)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="24" height="24" style={{ marginBottom: '4px' }}>
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                            <polyline points="17 8 12 3 7 8"></polyline>
                            <line x1="12" y1="3" x2="12" y2="15"></line>
                        </svg>
                        <span style={{ color: 'var(--c-b2bfd0)', fontSize: '13px', fontWeight: '500' }}>
                            Drag & drop complaint document here
                        </span>
                        <span style={{ color: 'var(--c-a99ade)', fontSize: '13px', fontWeight: '500' }}>
                            or click to browse
                        </span>
                    </>
                )}
            </div>
        </div>
    );
};

export default FileUpload;
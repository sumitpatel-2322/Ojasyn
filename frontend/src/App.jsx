import React, { useState } from 'react';
import CopilotChat from './components/CopilotChat';
import ComplaintForm from './components/ComplaintForm';

const App = () => {
    // State to hold the currently active conversation ID
    const [activeConversationId, setActiveConversationId] = useState(null);
    // Temporary state for the input field
    const [loadId, setLoadId] = useState('');

    const handleLoadChat = (e) => {
        e.preventDefault();
        if (loadId.trim()) {
            setActiveConversationId(loadId.trim());
        }
    };

    const handleNewChat = () => {
        setActiveConversationId(null);
        setLoadId('');
    };

    return (
        <>
            <style>{`
                *{
                    box-sizing:border-box;
                }

                body{
                    margin:0;
                    background:#edf2f7;
                    font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
                    overflow:hidden;
                }

                #root{
                    height:100vh;
                    width:100vw;
                    display: flex;
                    flex-direction: column;
                }

                /* --- NEW TOP BAR STYLES --- */
                .top-bar {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    padding: 12px 24px;
                    background: white;
                    border-bottom: 1px solid #e7ebf2;
                    box-shadow: 0 2px 4px rgba(15,23,42,.02);
                    z-index: 10;
                }

                .app-shell{
                    flex: 1;
                    width:100%;
                    display:flex;
                    padding:18px;
                    gap:18px;
                    background:
                        radial-gradient(circle at top left,#ffffff,#eef3f9 40%,#e7edf5 100%);
                    overflow: hidden;
                }

                .chat-panel{
                    width:430px;
                    min-width:430px;
                    max-width:430px;
                    display:flex;
                    flex-direction:column;
                    background:white;
                    border-radius:22px;
                    overflow:hidden;
                    border:1px solid #e7ebf2;
                    box-shadow:
                        0 10px 35px rgba(15,23,42,.08),
                        0 2px 8px rgba(15,23,42,.05);
                }

                .form-panel{
                    flex:1;
                    display:flex;
                    flex-direction:column;
                    background:white;
                    border-radius:22px;
                    overflow:hidden;
                    border:1px solid #e7ebf2;
                    box-shadow:
                        0 10px 35px rgba(15,23,42,.08),
                        0 2px 8px rgba(15,23,42,.05);
                }

                @media(max-width:1200px){
                    .app-shell{
                        flex-direction:column;
                        overflow:hidden;
                    }
                    .chat-panel{
                        width:100%;
                        min-width:100%;
                        max-width:100%;
                        height:42%;
                    }
                    .form-panel{
                        height:58%;
                    }
                }
            `}</style>

            {/* --- NEW DEVELOPMENT/TESTING NAV BAR --- */}
            <div className="top-bar">
                <div style={{ fontWeight: '800', color: '#0f172a', fontSize: '18px', letterSpacing: '-0.5px' }}>
                    OJASYN <span style={{ color: '#64748b', fontWeight: '500', fontSize: '14px', marginLeft: '8px' }}>Complaint Investigation Workspace</span>
                </div>
                
                <form onSubmit={handleLoadChat} style={{ display: 'flex', gap: '8px' }}>
                    <input
                        value={loadId}
                        onChange={(e) => setLoadId(e.target.value)}
                        placeholder="Enter Conversation ID to load..."
                        style={{ 
                            padding: '8px 12px', 
                            borderRadius: '6px', 
                            border: '1px solid #cbd5e1',
                            width: '250px',
                            fontSize: '13px',
                            outline: 'none'
                        }}
                    />
                    <button 
                        type="submit" 
                        style={{ 
                            padding: '8px 16px', 
                            borderRadius: '6px', 
                            background: '#3b82f6', 
                            color: 'white', 
                            border: 'none', 
                            cursor: 'pointer',
                            fontWeight: '600',
                            fontSize: '13px'
                        }}>
                        Load Chat
                    </button>
                    <button 
                        type="button" 
                        onClick={handleNewChat}
                        style={{ 
                            padding: '8px 16px', 
                            borderRadius: '6px', 
                            background: '#f1f5f9', 
                            color: '#0f172a', 
                            border: '1px solid #cbd5e1', 
                            cursor: 'pointer',
                            fontWeight: '600',
                            fontSize: '13px'
                        }}>
                        New Complaint
                    </button>
                </form>
            </div>

            <div className="app-shell">
                <div className="chat-panel">
                    {/* Pass the ID down so CopilotChat knows when to trigger fetchHistory */}
                    <CopilotChat conversationId={activeConversationId} />
                </div>

                <div className="form-panel">
                    <ComplaintForm />
                </div>
            </div>
        </>
    );
};

export default App;
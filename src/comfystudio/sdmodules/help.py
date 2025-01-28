import os
from qtpy.QtWidgets import (
    QDialog, QSplitter, QListWidget, QTextBrowser, QVBoxLayout, QMessageBox
)
from qtpy.QtCore import Qt, QUrl
from qtpy.QtGui import QDesktopServices

class HelpWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Help")
        self.resize(800, 600)
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Horizontal)

        # Sidebar for topics
        self.topicList = QListWidget()
        self.topicList.setMaximumWidth(200)
        self.topicList.currentRowChanged.connect(self.displayTopic)

        # Content area
        self.contentBrowser = QTextBrowser()
        self.contentBrowser.setOpenExternalLinks(True)
        self.contentBrowser.setOpenLinks(False)  # Handle links manually
        self.contentBrowser.anchorClicked.connect(self.handleLinkClicked)

        splitter.addWidget(self.topicList)
        splitter.addWidget(self.contentBrowser)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter)

        self.loadTopics()

    def loadTopics(self):
        help_folder = os.path.join(os.path.dirname(__file__), "..", "help")
        index_file = os.path.join(help_folder, "index.html")

        if not os.path.exists(index_file):
            QMessageBox.critical(self, "Error", f"Help index file not found at {index_file}")
            self.close()
            return

        with open(index_file, "r", encoding="utf-8") as f:
            # Assume that each topic is a link in the index.html
            # Parse the HTML to extract topic titles and corresponding files
            from bs4 import BeautifulSoup  # Requires `beautifulsoup4` package
            soup = BeautifulSoup(f, "html.parser")
            links = soup.find_all("a")
            self.topics = []
            for link in links:
                title = link.get_text()
                href = link.get("href")
                if href and href.endswith(".html"):
                    self.topics.append((title, href))

        for title, href in self.topics:
            self.topicList.addItem(title)

        if self.topics:
            self.topicList.setCurrentRow(0)

    def displayTopic(self, index):
        if index < 0 or index >= len(self.topics):
            return
        title, href = self.topics[index]
        help_folder = os.path.join(os.path.dirname(__file__), "..", "help")
        topic_file = os.path.join(help_folder, href)
        if os.path.exists(topic_file):
            with open(topic_file, "r", encoding="utf-8") as f:
                html_content = f.read()
                self.contentBrowser.setHtml(html_content)
        else:
            self.contentBrowser.setHtml(f"<h1>{title}</h1><p>Content not found.</p>")

    def handleLinkClicked(self, url: QUrl):
        if url.scheme() in ['http', 'https']:
            QDesktopServices.openUrl(url)
        else:
            # Handle internal links by finding the topic and displaying it
            path = url.path().lstrip('/')
            for i, (title, href) in enumerate(self.topics):
                if href == path:
                    self.topicList.setCurrentRow(i)
                    return
            # If not found, open in default browser
            QDesktopServices.openUrl(url)

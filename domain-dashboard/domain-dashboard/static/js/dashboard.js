    const ITEMS_PER_PAGE = 50
    let currentPage = 1
    let allRows = []
    let filteredRows = []
    let verifiedFilterActive = false
    
    const pendingChanges = {}
    const initialStates = {}
    let currentModalDomId = null
    let currentEditDomId = null
    
    // ===================== ADD DOMAIN STATE =====================
    let addSelectedCategory = ''
    
    // ===================== INIT =====================
    document.addEventListener('DOMContentLoaded', function () {
      document.querySelectorAll('tr[id^="row_"]').forEach((row) => {
        const id = row.id.split('_')[1]
        initialStates[id] = {
          verified: row.dataset.initialVerified === 'true',
          category: row.dataset.initialCategory,
          gitPush: row.dataset.gitPush === 'True'
        }
        allRows.push(row)
      })
    
      filteredRows = [...allRows]
      displayPage(1)
      updateUI()
    
      const searchInput = document.getElementById('search-input')
      if (searchInput) {
        searchInput.addEventListener('input', function () {
          const searchTerm = this.value.toLowerCase().trim()
          filterRows(searchTerm)
        })
      }
    
      const modalSearch = document.getElementById('modalSearch')
      if (modalSearch) {
        modalSearch.addEventListener('input', function () {
          const searchTerm = this.value.toLowerCase()
          document.querySelectorAll('.category-item').forEach(function (item) {
            const text = item.textContent.toLowerCase()
            item.style.display = text.includes(searchTerm) ? 'block' : 'none'
          })
        })
      }
    
      // Add domain category search
      const addCatSearch = document.getElementById('add-category-search')
      if (addCatSearch) {
        addCatSearch.addEventListener('input', function () {
          const term = this.value.toLowerCase()
          document.querySelectorAll('.add-cat-item').forEach((item) => {
            item.style.display = item.textContent.toLowerCase().includes(term) ? 'block' : 'none'
          })
        })
      }
    })
    
    // ===================== ADD DOMAIN =====================
    function selectAddCategory(cat) {
      addSelectedCategory = cat
    
      // Highlight selected item
      document.querySelectorAll('.add-cat-item').forEach((item) => {
        item.classList.toggle('selected', item.dataset.value === cat)
      })
    
      // Show badge
      const badge = document.getElementById('add-selected-category')
      badge.textContent = '✓ Selected: ' + cat
      badge.style.display = 'block'
    
      // Clear error
      document.getElementById('add-category-error').style.display = 'none'
    }
    
    function resetAddDomainModal() {
      addSelectedCategory = ''
      document.getElementById('add-domain-input').value = ''
      document.getElementById('add-category-search').value = ''
      document.getElementById('add-domain-error').style.display = 'none'
      document.getElementById('add-category-error').style.display = 'none'
      document.getElementById('add-selected-category').style.display = 'none'
      document.getElementById('add-selected-category').textContent = ''
      document.querySelectorAll('.add-cat-item').forEach((item) => {
        item.classList.remove('selected')
        item.style.display = 'block'
      })
      const btn = document.getElementById('add-domain-confirm-btn')
      btn.disabled = false
      btn.textContent = 'Add Domain'
    }
    
    function confirmAddDomain() {
      const domain = document.getElementById('add-domain-input').value.trim()
      let valid = true
    
      // Validate domain
      const domErr = document.getElementById('add-domain-error')
      if (!domain) {
        domErr.textContent = 'Domain name is required.'
        domErr.style.display = 'block'
        valid = false
      } else {
        domErr.style.display = 'none'
      }
    
      // Validate category
      const catErr = document.getElementById('add-category-error')
      if (!addSelectedCategory) {
        catErr.textContent = 'Please select a category.'
        catErr.style.display = 'block'
        valid = false
      } else {
        catErr.style.display = 'none'
      }
    
      if (!valid) return
    
      const btn = document.getElementById('add-domain-confirm-btn')
      btn.disabled = true
      btn.textContent = 'Adding...'
    
      fetch('/add_domain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain: domain, category: addSelectedCategory })
      })
        .then((res) => res.json())
        .then((data) => {
          btn.disabled = false
          btn.textContent = 'Add Domain'
          if (data.status === 'success') {
            closeModal('addDomainModal')
            // Show success modal
            document.getElementById('add-success-content').innerHTML = `
                  <div style="font-size: 40px; margin-bottom: 10px;">✅</div>
                  <div style="font-weight: 700; font-size: 16px; color: #28a745; margin-bottom: 8px;">Domain added successfully!</div>
                  <div style="font-size: 14px; color: #555; margin-bottom: 4px;"><strong>${data.domain.domain}</strong></div>
                  <div style="font-size: 13px; color: #888;">Category: ${data.domain.category}</div>
                  <div style="font-size: 12px; color: #aaa; margin-top: 8px;">Domain is marked as Verified and ready to push.</div>
                `
            openModal('addDomainSuccessModal')
            resetAddDomainModal()
          } else {
            const domErr = document.getElementById('add-domain-error')
            domErr.textContent = data.message
            domErr.style.display = 'block'
          }
        })
        .catch((err) => {
          btn.disabled = false
          btn.textContent = 'Add Domain'
          const domErr = document.getElementById('add-domain-error')
          domErr.textContent = 'Network error: ' + err.message
          domErr.style.display = 'block'
        })
    }
    
    function closeAddSuccessModal() {
      closeModal('addDomainSuccessModal')
      openModal('addDomainModal')
    }
    
    function closeAddSuccessAndReload() {
      closeModal('addDomainSuccessModal')
      location.reload()
    }
    
    // ===================== EXISTING LOGIC =====================
    function handleButtonClick(id, action) {
      const correctBtn = document.getElementById(`correct_btn_${id}`)
      const wrongBtn = document.getElementById(`wrong_btn_${id}`)
      const deleteBtn = document.getElementById(`delete_btn_${id}`)
    
      correctBtn.classList.remove('active')
      wrongBtn.classList.remove('active')
      deleteBtn.classList.remove('active')
      wrongBtn.dataset.selectedCategory = ''
    
      if (action === 'correct') {
        correctBtn.classList.add('active')
      } else if (action === 'wrong') {
        currentModalDomId = id
        openModal('categoryModal')
        return
      } else if (action === 'delete') {
        deleteBtn.classList.add('active')
      }
    
      updateStatus(id)
      calculatePendingChanges(id)
      updateUI()
    }
    
    function openModal(modalId) {
      document.getElementById(modalId).style.display = 'flex'
      if (modalId === 'categoryModal') {
        document.getElementById('modalSearch').value = ''
        $('.category-item').show()
        document.getElementById('modalSearch').focus()
      } else if (modalId === 'editDomainModal') {
        document.getElementById('edit-domain-input').focus()
      } else if (modalId === 'addDomainModal') {
        setTimeout(() => document.getElementById('add-domain-input').focus(), 100)
      }
    }
    
    function closeModal(modalId) {
      document.getElementById(modalId).style.display = 'none'
      if (modalId === 'categoryModal') {
        currentModalDomId = null
      } else if (modalId === 'editDomainModal') {
        currentEditDomId = null
      } else if (modalId === 'addDomainModal') {
        resetAddDomainModal()
      }
    }
    
    function selectCategory(category) {
      if (currentModalDomId === null) return
    
      const id = currentModalDomId
      const wrongBtn = document.getElementById(`wrong_btn_${id}`)
      const categoryCell = document.getElementById(`current_category_${id}`)
    
      if (category) {
        categoryCell.textContent = category
        if (category !== initialStates[id].category) {
          categoryCell.style.fontStyle = 'italic'
          categoryCell.style.color = '#2196F3'
        }
        wrongBtn.dataset.selectedCategory = category
        wrongBtn.classList.add('active')
      } else {
        wrongBtn.dataset.selectedCategory = ''
        wrongBtn.classList.remove('active')
      }
    
      updateStatus(id)
      calculatePendingChanges(id)
      updateUI()
      closeModal('categoryModal')
    }
    
    function openEditDomainModal(id, currentDomain) {
      currentEditDomId = id
      document.getElementById('edit-domain-input').value = currentDomain
      openModal('editDomainModal')
    }
    
    function confirmEditDomain() {
      const newDomain = document.getElementById('edit-domain-input').value.trim()
      if (!newDomain) {
        alert('Domain cannot be empty.')
        return
      }
      const oldDomain = document.getElementById(`domain_link_${currentEditDomId}`).textContent
      if (newDomain === oldDomain) {
        closeModal('editDomainModal')
        return
      }
    
      fetch(`/update_domain/${currentEditDomId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `new_domain=${encodeURIComponent(newDomain)}`
      })
        .then((response) => response.json())
        .then((data) => {
          if (data.status === 'success') {
            const link = document.getElementById(`domain_link_${currentEditDomId}`)
            link.href = `https://${newDomain}`
            link.textContent = newDomain
            closeModal('editDomainModal')
          } else {
            alert(data.message)
          }
        })
        .catch((error) => {
          alert('Error updating domain: ' + error.message)
        })
    }
    
    window.onclick = function (event) {
      if (event.target.classList.contains('modal')) {
        const modalId = event.target.id
        event.target.style.display = 'none'
        if (modalId === 'categoryModal') currentModalDomId = null
        else if (modalId === 'editDomainModal') currentEditDomId = null
        else if (modalId === 'addDomainModal') resetAddDomainModal()
      }
    }
    
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        document.querySelectorAll('.modal').forEach((modal) => {
          modal.style.display = 'none'
        })
        currentModalDomId = null
        currentEditDomId = null
        resetAddDomainModal()
      }
    })
    
    function updateStatus(id) {
      const correctBtn = document.getElementById(`correct_btn_${id}`)
      const wrongBtn = document.getElementById(`wrong_btn_${id}`)
      const deleteBtn = document.getElementById(`delete_btn_${id}`)
      const statusCell = document.getElementById(`status_${id}`)
      const categoryCell = document.getElementById(`current_category_${id}`)
      const row = document.getElementById(`row_${id}`)
    
      categoryCell.textContent = initialStates[id].category
      categoryCell.style.fontStyle = 'normal'
      categoryCell.style.color = ''
    
      if (correctBtn.classList.contains('active')) {
        statusCell.innerHTML = '<span class="status-verified">Verified</span>'
        row.style.backgroundColor = '#e8f5e9'
      } else if (wrongBtn.classList.contains('active')) {
        const selectedCategory = wrongBtn.dataset.selectedCategory
        if (selectedCategory) {
          categoryCell.textContent = selectedCategory
          if (selectedCategory !== initialStates[id].category) {
            categoryCell.style.fontStyle = 'italic'
            categoryCell.style.color = '#2196F3'
          }
          statusCell.innerHTML = '<span class="status-verified">Verified</span>'
          row.style.backgroundColor = '#e8f5e9'
        } else {
          statusCell.innerHTML = '<span class="status-pending">Pending</span>'
          row.style.backgroundColor = ''
        }
      } else if (deleteBtn.classList.contains('active')) {
        statusCell.innerHTML = '<span class="status-delete">Will be deleted</span>'
        row.style.backgroundColor = '#ffe6e6'
      } else {
        if (initialStates[id].verified) {
          statusCell.innerHTML = '<span class="status-verified">Verified</span>'
          row.style.backgroundColor = '#e8f5e9'
        } else {
          statusCell.innerHTML = '<span class="status-pending">Pending</span>'
          row.style.backgroundColor = ''
        }
      }
    }
    
    function calculatePendingChanges(id) {
      const correctBtn = document.getElementById(`correct_btn_${id}`)
      const wrongBtn = document.getElementById(`wrong_btn_${id}`)
      const deleteBtn = document.getElementById(`delete_btn_${id}`)
      const selectedCategory = wrongBtn.dataset.selectedCategory
      const initial = initialStates[id]
    
      delete pendingChanges[id]
    
      if (correctBtn.classList.contains('active')) {
        if (!initial.verified) {
          pendingChanges[id] = { action: 'correct', category: null }
        }
      } else if (wrongBtn.classList.contains('active')) {
        if (selectedCategory) {
          if (!initial.verified || selectedCategory !== initial.category) {
            pendingChanges[id] = { action: 'change', category: selectedCategory }
          }
        }
      } else if (deleteBtn.classList.contains('active')) {
        pendingChanges[id] = { action: 'discard', category: null }
      } else if (initial.verified) {
        pendingChanges[id] = { action: 'unverify', category: null }
      }
    }
    
    function updateUI() {
      const changeCount = Object.keys(pendingChanges).length
      const changesCountSpan = document.getElementById('changes-count')
      const changesStat = document.getElementById('changes-stat')
      const btnCount = document.getElementById('btn-count')
      const applyBtn = document.getElementById('apply-btn')
    
      if (changeCount > 0) {
        changesCountSpan.textContent = changeCount
        changesStat.style.display = 'flex'
        btnCount.textContent = `(${changeCount})`
        applyBtn.style.background = '#FF9800'
      } else {
        changesStat.style.display = 'none'
        btnCount.textContent = ''
        applyBtn.style.background = '#4CAF50'
      }
    }
    
    function showApplyModal() {
      const changeCount = Object.keys(pendingChanges).length
    
      if (changeCount === 0) {
        alert('No changes to apply.')
        return
      }
    
      const toDelete = Object.values(pendingChanges).filter((c) => c.action === 'discard').length
      const toVerify = Object.values(pendingChanges).filter((c) => c.action === 'correct' || c.action === 'change').length
      const toUnverify = Object.values(pendingChanges).filter((c) => c.action === 'unverify').length
    
      let summaryHTML = `<div style="font-size: 14px; color: #333;">`
      summaryHTML += `<p style="margin-bottom: 15px; font-weight: 600; font-size: 16px;">You are about to apply ${changeCount} change(s):</p>`
      summaryHTML += `<ul style="list-style: none; padding: 0; margin: 0;">`
    
      if (toVerify > 0) summaryHTML += `<li style="padding: 8px 0; border-bottom: 1px solid #f0f0f0;"><span style="color: #28a745; font-weight: 600;">✓</span> Verify <strong>${toVerify}</strong> domain(s)</li>`
      if (toUnverify > 0) summaryHTML += `<li style="padding: 8px 0; border-bottom: 1px solid #f0f0f0;"><span style="color: #ff9800; font-weight: 600;">↺</span> Unverify <strong>${toUnverify}</strong> domain(s)</li>`
      if (toDelete > 0) summaryHTML += `<li style="padding: 8px 0;"><span style="color: #dc3545; font-weight: 600;">✗</span> Delete <strong>${toDelete}</strong> domain(s)</li>`
    
      summaryHTML += `</ul></div>`
    
      document.getElementById('apply-summary').innerHTML = summaryHTML
      openModal('applyModal')
    }
    
    function confirmApplyChanges() {
      closeModal('applyModal')
    
      const promises = []
      const changeCount = Object.keys(pendingChanges).length
      const toDelete = Object.values(pendingChanges).filter((c) => c.action === 'discard').length
    
      for (const [id, change] of Object.entries(pendingChanges)) {
        let body
        if (change.action === 'correct') body = 'action=correct'
        else if (change.action === 'change') body = `action=change&category=${encodeURIComponent(change.category)}`
        else if (change.action === 'discard') body = 'action=discard'
        else if (change.action === 'unverify') body = 'action=unverify'
    
        promises.push(
          fetch(`/verify/${id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: body
          })
        )
      }
    
      Promise.all(promises)
        .then((responses) => {
          const allSuccessful = responses.every((r) => r.ok)
          if (allSuccessful) {
            let message = `<div style="color: #28a745; font-size: 16px; font-weight: 600; margin-bottom: 10px;">Successfully applied ${changeCount} change(s)!</div>`
            if (toDelete > 0) message += `<div style="color: #666; font-size: 14px;">Deleted ${toDelete} domain(s).</div>`
            document.getElementById('apply-success-content').innerHTML = message
            openModal('applySuccessModal')
          } else {
            alert('Some changes failed to apply. Please try again.')
          }
        })
        .catch((error) => {
          alert('Error applying changes: ' + error.message)
        })
    }
    
    function closeSuccessAndReload() {
      closeModal('applySuccessModal')
      location.reload()
    }
    
    function showPushModal() {
      openModal('pushModal')
    }
    
    function confirmGitPush() {
      closeModal('pushModal')
      openModal('pushProgressModal')
    
      fetch(window.APP_URLS.pushToGit, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      })
        .then((response) => response.json())
        .then((data) => {
          closeModal('pushProgressModal')
          showPushResult(data)
        })
        .catch((error) => {
          closeModal('pushProgressModal')
          showPushResult({ status: 'error', message: 'Network error: ' + error.message })
        })
    }
    
    function showPushResult(data) {
      const titleEl = document.getElementById('result-modal-title')
      const contentEl = document.getElementById('push-result-content')
    
      if (data.status === 'success') {
        titleEl.textContent = '✓ Push Successful'
        titleEl.style.color = '#28a745'
        contentEl.innerHTML = `
              <div style="color: #28a745; font-size: 16px; font-weight: 600; margin-bottom: 15px;">Successfully pushed to Git repository!</div>
              <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; font-size: 13px;">
                <div style="margin-bottom: 10px;"><strong>Files Updated:</strong> ${data.details.files_updated.length}</div>
                <div style="margin-bottom: 10px;">
                  <strong>Categories:</strong>
                  <div style="margin-top: 5px; max-height: 150px; overflow-y: auto;">
                    ${data.details.files_updated.map((f) => `<div style="padding: 3px 0;">• ${f}</div>`).join('')}
                  </div>
                </div>
                <div style="margin-bottom: 10px;"><strong>Domains Added:</strong> ${data.details.domains_added}</div>
                <div><strong>Commit SHA:</strong> <code>${data.details.commit_sha}</code></div>
              </div>`
      } else {
        titleEl.textContent = '✗ Push Failed'
        titleEl.style.color = '#dc3545'
        let html = `<div style="color: #dc3545; font-size: 16px; font-weight: 600; margin-bottom: 15px;">${data.message}</div>`
        if (data.error_type) {
          html += `<div style="background: #fff3cd; padding: 15px; border-radius: 8px; border-left: 4px solid #ffc107;">
                <div style="font-weight: 600; margin-bottom: 8px;">Error Details:</div>
                <div style="font-size: 13px; color: #856404;"><strong>Type:</strong> ${data.error_type}</div>
              </div>`
        }
        html += `<div style="margin-top: 15px; padding: 12px; background: #f8f9fa; border-radius: 6px; font-size: 13px; color: #666;">
              <strong>Common Issues:</strong>
              <ul style="margin: 8px 0 0 20px; padding: 0;">
                <li>Check if the Git repository path is correct</li>
                <li>Ensure you have write permissions</li>
                <li>Verify network connectivity for remote push</li>
                <li>Check if category files exist in the repository</li>
              </ul>
            </div>`
        contentEl.innerHTML = html
      }
      openModal('pushResultModal')
    }
    
    function closeResultAndReload() {
      closeModal('pushResultModal')
      location.reload()
    }
    
    function toggleVerifiedFilter() {
      verifiedFilterActive = !verifiedFilterActive
      const btn = document.getElementById('verified-filter-btn')
      btn.textContent = verifiedFilterActive ? 'Show All' : 'Show Verified Only'
      btn.classList.toggle('active', verifiedFilterActive)
      filterRows(document.getElementById('search-input').value.toLowerCase().trim())
    }
    
    function isCurrentlyVerified(id) {
      const correctBtn = document.getElementById(`correct_btn_${id}`)
      const wrongBtn = document.getElementById(`wrong_btn_${id}`)
      if (correctBtn.classList.contains('active')) return true
      if (wrongBtn.classList.contains('active') && wrongBtn.dataset.selectedCategory) return true
      return initialStates[id].verified
    }
    
    function filterRows(searchTerm) {
      let matchedRows = []
    
      allRows.forEach((row) => {
        const id = row.id.split('_')[1]
        const categoryCell = document.getElementById(`current_category_${id}`)
        const statusCell = document.getElementById(`status_${id}`)
        const category = categoryCell.textContent.toLowerCase()
        const status = statusCell.textContent.toLowerCase()
    
        const matchesSearch = searchTerm === '' || category.includes(searchTerm) || status.includes(searchTerm)
        let matchesFilter = true
        if (verifiedFilterActive) {
          matchesFilter = isCurrentlyVerified(id) && !initialStates[id].gitPush
        }
    
        if (matchesSearch && matchesFilter) matchedRows.push({ row, category, status })
      })
    
      if (searchTerm !== '' && matchedRows.length > 0) {
        matchedRows.sort((a, b) => {
          const aCatMatch = a.category.includes(searchTerm)
          const bCatMatch = b.category.includes(searchTerm)
          if (aCatMatch && !bCatMatch) return -1
          if (!aCatMatch && bCatMatch) return 1
          return a.category.localeCompare(b.category)
        })
      }
    
      filteredRows = matchedRows.map((item) => item.row)
      displayPage(1)
    }
    
    function displayPage(page) {
      const startIndex = (page - 1) * ITEMS_PER_PAGE
      const endIndex = startIndex + ITEMS_PER_PAGE
    
      allRows.forEach((row) => (row.style.display = 'none'))
      const rowsToShow = filteredRows.slice(startIndex, endIndex)
      rowsToShow.forEach((row) => (row.style.display = ''))
      rowsToShow.forEach((row, index) => {
        row.cells[0].textContent = startIndex + index + 1
      })
    
      updatePaginationInfo(startIndex, endIndex)
      updatePaginationControls(page)
      currentPage = page
    }
    
    function updatePaginationInfo(startIndex, endIndex) {
      const totalItems = filteredRows.length
      document.getElementById('showing-start').textContent = totalItems > 0 ? startIndex + 1 : 0
      document.getElementById('showing-end').textContent = Math.min(endIndex, totalItems)
      document.getElementById('total-items').textContent = totalItems
    }
    
    function updatePaginationControls(page) {
      const totalPages = Math.ceil(filteredRows.length / ITEMS_PER_PAGE)
    
      document.getElementById('prev-btn').disabled = page === 1
      document.getElementById('next-btn').disabled = page === totalPages || totalPages === 0
    
      const pageNumbersContainer = document.getElementById('page-numbers')
      pageNumbersContainer.innerHTML = ''
    
      if (totalPages <= 7) {
        for (let i = 1; i <= totalPages; i++) pageNumbersContainer.appendChild(createPageButton(i, page))
      } else {
        pageNumbersContainer.appendChild(createPageButton(1, page))
        if (page > 3) pageNumbersContainer.appendChild(createEllipsis())
        for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) {
          pageNumbersContainer.appendChild(createPageButton(i, page))
        }
        if (page < totalPages - 2) pageNumbersContainer.appendChild(createEllipsis())
        pageNumbersContainer.appendChild(createPageButton(totalPages, page))
      }
    }
    
    function createPageButton(pageNum, currentPage) {
      const btn = document.createElement('button')
      btn.className = 'page-btn' + (pageNum === currentPage ? ' active' : '')
      btn.textContent = pageNum
      btn.onclick = () => displayPage(pageNum)
      return btn
    }
    
    function createEllipsis() {
      const span = document.createElement('span')
      span.className = 'page-ellipsis'
      span.textContent = '...'
      return span
    }
    
    function changePage(direction) {
      const totalPages = Math.ceil(filteredRows.length / ITEMS_PER_PAGE)
      const newPage = currentPage + direction
      if (newPage >= 1 && newPage <= totalPages) {
        displayPage(newPage)
        document.querySelector('.card').scrollIntoView({ behavior: 'smooth' })
      }
    }
